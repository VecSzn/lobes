"""Tools the motor lobe can pick. Every result is a dict that gets written to the run dir verbatim; the text the
verifier can quote is always under "stdout" or "content".

There is no sandbox. python and shell run whatever the model wrote, as you, with a 10 s timeout; only the file
tools are confined, to the work dir. Run the whole process in a container on a machine you do not trust."""
import re
import subprocess
import sys
from pathlib import Path

import httpx

TIMEOUT = 10
MAX_OUT = 8000     # chars kept per stream; the rest is still on disk


def _run(argv, workdir, shell=False):
    try:
        p = subprocess.run(argv, cwd=workdir, shell=shell, capture_output=True, text=True, timeout=TIMEOUT,
                           encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired as e:
        return {"stdout": (e.stdout or "")[-MAX_OUT:], "stderr": f"timeout after {TIMEOUT}s", "exit": -1}
    return {"stdout": p.stdout[-MAX_OUT:], "stderr": p.stderr[-MAX_OUT:], "exit": p.returncode}


def _unescape(code):
    """granite writes newlines as a literal backslash-n inside the json string. Such a one-liner still compiles
    when a # comment swallows the rest, so without a real newline the unescaped text is tried first."""
    fixed = code.replace("\\n", "\n").replace("\\t", "\t").replace('\\"', '"')
    for c in ((fixed, code) if "\n" not in code else (code, fixed)):
        try:
            compile(c, "<tool>", "exec")
            return c
        except SyntaxError:
            pass
    return code


def python(code: str, workdir: Path):
    code = _unescape(code)
    res = _run([sys.executable, "-I", "-c", code], workdir)
    if res["exit"] == 0 and not res["stdout"].strip() and "print" not in code:
        # small models write `17 * 23` and expect the value back; show the last expression like a REPL would
        head, _, last = code.rstrip().rpartition("\n")
        again = _run([sys.executable, "-I", "-c", f"{head}\nprint(repr({last}))"], workdir)
        if again["exit"] == 0:
            return again
    return res


def shell(command: str, workdir: Path):
    return _run(command, workdir, shell=True)


def _inside(path, workdir):
    """Writes stay in the run's work dir."""
    p = Path(path)
    p = (p if p.is_absolute() else workdir / p).resolve()
    return p if workdir.resolve() in (p, *p.parents) else None


def read_file(path: str, workdir: Path):
    p = _inside(path, workdir)
    if p is None:
        return {"stderr": f"{path} is outside the work dir", "exit": 1}
    try:
        return {"content": p.read_text(encoding="utf-8", errors="replace")[:MAX_OUT * 4], "exit": 0}
    except OSError as e:
        return {"stderr": str(e), "exit": 1}


def write_file(path: str, content: str, workdir: Path):
    p = _inside(path, workdir)
    if p is None:
        return {"stderr": f"{path} is outside the work dir", "exit": 1}
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return {"content": f"wrote {len(content)} chars to {p.name}", "exit": 0}


def edit_file(path: str, old: str, new: str, workdir: Path):
    p = _inside(path, workdir)
    if p is None or not p.exists():
        return {"stderr": f"{path} not found in the work dir", "exit": 1}
    text = p.read_text(encoding="utf-8")
    if text.count(old) != 1:
        return {"stderr": f"old text occurs {text.count(old)} times, need exactly 1", "exit": 1}
    p.write_text(text.replace(old, new), encoding="utf-8")
    return {"content": f"edited {p.name}", "exit": 0}


def web_fetch(url: str, workdir: Path):
    try:
        r = httpx.get(url, timeout=15, follow_redirects=True, headers={"User-Agent": "lobes/0.0"})
    except httpx.HTTPError as e:
        return {"stderr": str(e), "exit": 1}
    text = r.text
    if "html" in r.headers.get("content-type", ""):
        text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text, flags=re.S | re.I)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", text))
    return {"content": text[:MAX_OUT * 4], "status": r.status_code, "exit": 0 if r.status_code < 400 else 1}


def screenshot(workdir: Path):
    from PIL import ImageGrab
    n = len(list(workdir.glob("shot_*.png")))
    p = workdir / f"shot_{n}.png"
    ImageGrab.grab().save(p)
    return {"image": str(p), "content": f"screenshot saved as {p.name}", "exit": 0}


TOOLS = {   # name: (fn, arg schema, what the motor lobe is told)
    "python": (python, {"code": {"type": "string"}}, "run python source, you get stdout back"),
    "shell": (shell, {"command": {"type": "string"}}, "run one shell command (10 s limit)"),
    "read_file": (read_file, {"path": {"type": "string"}}, "read a text file in the work dir"),
    "write_file": (write_file, {"path": {"type": "string"}, "content": {"type": "string"}}, "write a text file in the work dir"),
    "edit_file": (edit_file, {"path": {"type": "string"}, "old": {"type": "string"}, "new": {"type": "string"}},
                  "replace one exact occurrence of old with new in a work dir file"),
    "web_fetch": (web_fetch, {"url": {"type": "string"}}, "fetch a url, html is reduced to text"),
    "screenshot": (screenshot, {}, "capture the screen; the perception lobe describes it"),
}


def describe():
    return "\n".join(f"{n}({', '.join(props)}): {doc}" for n, (_, props, doc) in TOOLS.items())


def call_schema(only=None):
    """JSON schema for one ToolCall, name and args tied together so the grammar cannot mix them up."""
    return {"oneOf": [
        {"type": "object",
         "properties": {"name": {"const": n},
                        "args": {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}},
         "required": ["name", "args"], "additionalProperties": False}
        for n, (_, props, _) in TOOLS.items() if only is None or n in only]}


def run(name: str, args: dict, workdir: Path):
    if name not in TOOLS:
        return {"stderr": f"no tool named {name}", "exit": 2}
    fn, props, _ = TOOLS[name]
    missing = [k for k in props if k not in args]
    if missing:
        return {"stderr": f"{name} needs {missing}", "exit": 2}
    return fn(**{k: args[k] for k in props}, workdir=workdir)


if __name__ == "__main__":
    import tempfile
    d = Path(tempfile.mkdtemp())
    assert run("python", {"code": "print(6*7)"}, d)["stdout"].strip() == "42"
    assert run("python", {"code": "x = 6\nx * 7"}, d)["stdout"].strip() == "42"
    assert run("python", {"code": "x = 6"}, d)["stdout"] == ""
    assert run("python", {"code": 'x = 6\\ny = 7\\nprint(f\\"{x*y}\\")'}, d)["stdout"].strip() == "42"
    assert run("python", {"code": 'print("a\\nb")'}, d)["stdout"] == "a\nb\n"
    assert run("python", {"code": "import time; time.sleep(30)"}, d)["exit"] == -1
    assert run("read_file", {"path": "nope.txt"}, d)["exit"] == 1
    assert run("nothing", {}, d)["exit"] == 2
    assert run("shell", {"command": "echo hi"}, d)["stdout"].strip() == "hi"
    assert run("write_file", {"path": "a.txt", "content": "x=1\n"}, d)["exit"] == 0
    assert run("write_file", {"path": "../a.txt", "content": ""}, d)["exit"] == 1
    assert run("read_file", {"path": __file__}, d)["exit"] == 1        # reads stay in the work dir, like writes
    assert run("edit_file", {"path": "a.txt", "old": "x=1", "new": "x=2"}, d)["exit"] == 0
    assert run("read_file", {"path": "a.txt"}, d)["content"] == "x=2\n"
    assert run("edit_file", {"path": "a.txt", "old": "zzz", "new": ""}, d)["exit"] == 1
    print("tools ok")
