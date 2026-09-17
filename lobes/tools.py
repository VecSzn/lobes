"""Tools the models call natively. Every result is a dict that gets written to the run dir verbatim; its text is
always under "stdout" or "content".

There is no sandbox. python and shell run whatever the model wrote, with a 10 s timeout; only the file tools are
confined, to the work dir. Running as root they drop to an unprivileged user first, so a machine-wide action fails;
running as yourself they have everything you have. Run the whole process in a container on a machine you do not trust."""
import ast
import html
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.parse
from pathlib import Path

import httpx

TIMEOUT = 10
MAX_OUT = 8000     # chars kept per stream; the rest is still on disk
UNPRIVILEGED = "nobody"      # asked to switch the machine off, the python tool did it to the evaluation box


def _drop():
    """-> the Popen keywords that run a child as an unprivileged user; empty when there is nothing to drop."""
    if os.name != "posix" or os.geteuid() != 0:
        return {}
    try:
        import pwd
        who = pwd.getpwnam(UNPRIVILEGED)
    except (ImportError, KeyError):
        return {}
    # the group as well: left in root's group the child is refused by a 0701 home directory it could otherwise enter
    return {"user": who.pw_uid, "group": who.pw_gid, "extra_groups": []}


def _writable(*paths):
    """The dropped user still writes where the tool runs."""
    for p in paths:
        try:
            os.chmod(p, 0o777 if Path(p).is_dir() else 0o666)
        except OSError:
            pass


def _run(argv, workdir, shell=False):
    drop = _drop()
    if drop:
        _writable(workdir)
    try:
        p = subprocess.run(argv, cwd=workdir, shell=shell, capture_output=True, text=True, timeout=TIMEOUT,
                           encoding="utf-8", errors="replace", **drop)
    except subprocess.TimeoutExpired as e:
        out = e.stdout.decode("utf-8", "replace") if isinstance(e.stdout, bytes) else e.stdout or ""   # bytes even with text=True
        return {"stdout": out[-MAX_OUT:], "stderr": f"timeout after {TIMEOUT}s", "exit": -1}
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
    if "print" not in code:
        # small models write `17 * 23` and expect the value back; show the last expression like a REPL would.
        # The syntax tree finds it (the last line of `x = round(\n  3.14,\n)` is only ")"); it is echoed in the one
        # run, since a second run repeats every write and POST, and from the model's own text
        try:
            last = ast.parse(code).body[-1]
        except (SyntaxError, RecursionError, ValueError, IndexError):     # the run shows what is wrong, if anything
            last = None
        if isinstance(last, ast.Expr) and (value := ast.get_source_segment(code, last.value)):
            lines = code.encode().split(b"\n")      # col_offset counts utf-8 bytes
            head = b"\n".join(lines[:last.lineno - 1] + [lines[last.lineno - 1][:last.col_offset]]).decode()
            code = f"{head}_ = ({value})\nif _ is not None:\n    print(repr(_))"
    key = Path(workdir).resolve()
    if key not in _interpreters:
        _interpreters[key] = _Interpreter(key)
    it = _interpreters[key]
    res = it.run(code)
    if it.proc.poll() is not None:      # timed out or crashed: the next call starts a new one
        close(key)
    return res


# Reads one json-encoded script per line, runs it in __main__'s globals and answers with its exit code. Output goes to
# two files, so what child processes print is kept too
_SERVE = r"""
def _serve():
    import json, os, sys, traceback
    ns, (out, err) = vars(sys.modules["__main__"]), sys.argv[1:]
    del ns["_serve"], sys.argv[1:]
    ask, answer = os.fdopen(os.dup(0), encoding="utf-8"), os.fdopen(os.dup(1), "w", encoding="utf-8")
    os.dup2(os.open(os.devnull, os.O_RDONLY), 0)
    for fd, path, stream in ((1, out, sys.stdout), (2, err, sys.stderr)):
        os.dup2(os.open(path, os.O_WRONLY | os.O_APPEND | getattr(os, "O_BINARY", 0)), fd)
        stream.reconfigure(encoding="utf-8")
    for line in ask:
        code = 0
        try:
            exec(compile(json.loads(line), "<string>", "exec"), ns)
        except SystemExit as e:
            if isinstance(e.code, str):
                print(e.code, file=sys.stderr)
            code = e.code if isinstance(e.code, int) else int(e.code is not None)
        except BaseException as e:
            traceback.print_exception(e.__class__, e, e.__traceback__.tb_next)
            code = 1
        sys.stdout, sys.stderr = sys.__stdout__, sys.__stderr__
        sys.stdout.flush()
        sys.stderr.flush()
        answer.write(f"{code}\n")
        answer.flush()
_serve()
"""
_interpreters = {}      # work dir -> _Interpreter


class _Interpreter:
    """One python process per request: small models define a function or import a module in one call and use it in
    the next, as in a notebook (NameError in 76 of 2469 calls on 09-17, 39 of them a module imported before)."""

    def __init__(self, workdir):
        self.dir = Path(tempfile.mkdtemp(prefix="lobes-python-"))
        self.out, self.err = self.dir / "out", self.dir / "err"
        self.out.touch()
        self.err.touch()
        drop = _drop()
        if drop:
            _writable(self.dir, self.out, self.err, workdir)
        self.proc = subprocess.Popen([sys.executable, "-I", "-u", "-c", _SERVE, str(self.out), str(self.err)], cwd=workdir,
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding="utf-8", **drop)
        self.done = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        with self.proc.stdout as lines:
            for line in lines:
                self.done.put(int(line))
        self.done.put(None)

    def run(self, code):
        start = self.out.stat().st_size, self.err.stat().st_size
        try:
            self.proc.stdin.write(json.dumps(code) + "\n")
            self.proc.stdin.flush()
            exit = self.done.get(timeout=TIMEOUT)
        except queue.Empty:
            self.proc.kill()
            self.proc.wait()
            return {"stdout": _tail(self.out, start[0]), "stderr": f"timeout after {TIMEOUT}s, the interpreter restarted",
                    "exit": -1}
        except OSError:         # it exited between calls
            exit = None
        if exit is None:
            exit = self.proc.wait()
        return {"stdout": _tail(self.out, start[0]), "stderr": _tail(self.err, start[1]), "exit": exit}


def _tail(path, start):
    with open(path, "rb") as f:
        f.seek(max(start, f.seek(0, 2) - 4 * MAX_OUT))
        text = f.read().decode("utf-8", "replace")
    return text.replace("\r\n", "\n").replace("\r", "\n")[-MAX_OUT:]     # the newlines subprocess text mode gave


def close(workdir):
    """Ends the python process of a finished request."""
    it = _interpreters.pop(Path(workdir).resolve(), None)
    if it is not None:
        it.proc.kill()
        it.proc.wait()
        it.proc.stdin.close()
        shutil.rmtree(it.dir, ignore_errors=True)


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


def _text(page):
    """Html down to the words in it."""
    page = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", page, flags=re.S | re.I)
    page = re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", re.sub(r"<[^>]+>", " ", page)))
    return html.unescape(page)


def web_fetch(url: str, workdir: Path):
    try:
        r = httpx.get(url, timeout=15, follow_redirects=True, headers={"User-Agent": "lobes/0.0"})
    except httpx.HTTPError as e:
        return {"stderr": str(e), "exit": 1}
    text = _text(r.text) if "html" in r.headers.get("content-type", "") else r.text
    return {"content": text[:MAX_OUT * 4], "status": r.status_code, "exit": 0 if r.status_code < 400 else 1}


# duckduckgo's page for clients without javascript. Every url on it goes through a redirect that carries the real
# one. Bing was tried first and is not usable: it answers a long-tail query with results for an unrelated one.
SEARCH = "https://html.duckduckgo.com/html/?q={}"
RESULT = re.compile(r'<a[^>]*class="result__(?P<part>a|snippet)"[^>]*href="(?P<href>[^"]*)"[^>]*>(?P<text>.*?)</a>', re.S)
TARGET = re.compile(r"[?&](?:amp;)?uddg=(?P<url>[^&\"]+)")


def web_search(query: str, workdir: Path):
    try:
        r = httpx.get(SEARCH.format(urllib.parse.quote_plus(query)), timeout=15, follow_redirects=True,
                      headers={"User-Agent": "lobes/0.0"})
    except httpx.HTTPError as e:
        return {"stderr": str(e), "exit": 1}
    found = []
    for m in RESULT.finditer(r.text):
        text = _text(m["text"]).strip()
        if m["part"] == "snippet":          # the snippet follows its own title and belongs to it
            if found:
                found[-1] += "\n" + text
            continue
        target = TARGET.search(m["href"])
        found.append(f"{text}\n{urllib.parse.unquote(target['url']) if target else m['href']}")
    if not found:                           # the markup moved, or the page is a challenge; it still says something
        return {"content": _text(r.text)[:MAX_OUT], "status": r.status_code, "exit": 1}
    return {"content": "\n\n".join(found[:8]), "status": r.status_code, "exit": 0}


def screenshot(workdir: Path):
    from PIL import ImageGrab
    n = len(list(workdir.glob("shot_*.png")))
    p = workdir / f"shot_{n}.png"
    ImageGrab.grab().save(p)
    return {"image": str(p), "content": f"screenshot saved as {p.name}", "exit": 0}


TOOLS = {   # name: (fn, arg schema, description the model sees)
    "python": (python, {"code": {"type": "string"}}, "Run a Python script. You get its stdout and stderr back."),
    "shell": (shell, {"command": {"type": "string"}}, "run one shell command (10 s limit)"),
    "read_file": (read_file, {"path": {"type": "string"}}, "read a text file in the work dir"),
    "write_file": (write_file, {"path": {"type": "string"}, "content": {"type": "string"}}, "write a text file in the work dir"),
    "edit_file": (edit_file, {"path": {"type": "string"}, "old": {"type": "string"}, "new": {"type": "string"}},
                  "replace one exact occurrence of old with new in a work dir file"),
    "web_search": (web_search, {"query": {"type": "string"}}, "search the web; you get a title, url and snippet each"),
    "web_fetch": (web_fetch, {"url": {"type": "string"}}, "fetch a url, html is reduced to text"),
    "screenshot": (screenshot, {}, "capture the screen; the perception lobe describes it"),
}


def spec(name, doc, props):
    return {"type": "function", "function": {"name": name, "description": doc, "parameters": {
        "type": "object", "properties": props, "required": list(props)}}}


def specs(only=None):
    """The openai tools list llama-server hands to the chat template."""
    return [spec(n, doc, props) for n, (_, props, doc) in TOOLS.items() if only is None or n in only]


def run(name: str, args: dict, workdir: Path):
    if name not in TOOLS:
        return {"stderr": f"no tool named {name}", "exit": 2}
    fn, props, _ = TOOLS[name]
    missing = [k for k in props if k not in args]
    if missing:
        return {"stderr": f"{name} needs {missing}", "exit": 2}
    try:
        return fn(**{k: args[k] for k in props}, workdir=workdir)
    except Exception as e:      # a directory as the path, a port that is not a number: the model reads why
        return {"stderr": f"{type(e).__name__}: {e}", "exit": 1}


if __name__ == "__main__":
    d = Path(tempfile.mkdtemp())
    assert run("python", {"code": "print(6*7)"}, d)["stdout"].strip() == "42"
    assert run("python", {"code": "x = 6\nx * 7"}, d)["stdout"].strip() == "42"
    assert run("python", {"code": "x = 6\nx, x * 7"}, d)["stdout"].strip() == "(6, 42)"
    assert run("python", {"code": "x = 6"}, d)["stdout"] == ""
    assert run("python", {"code": "x = round(\n    3.14159,\n)"}, d)["stdout"] == ""
    assert run("python", {"code": "x = round(\n    3.14159,\n)\nx"}, d)["stdout"].strip() == "3"
    assert run("python", {"code": "def f():\n    pass\nf()"}, d)["stdout"] == ""
    assert run("python", {"code": "open('n.txt', 'a').write(\n    'x',\n)"}, d)["stdout"].strip() == "1" and (d / "n.txt").read_text() == "x"
    assert run("python", {"code": "t = " + " + ".join(["1"] * 400) + "\nt"}, d)["stdout"].strip() == "400"
    assert run("python", {"code": "import math; math.pi  # pi"}, d)["stdout"].strip() == "3.141592653589793"
    assert run("web_fetch", {"url": "http://localhost:PORT/x"}, d)["exit"] == 1
    assert run("python", {"code": 'x = 6\\ny = 7\\nprint(f\\"{x*y}\\")'}, d)["stdout"].strip() == "42"
    assert run("python", {"code": 'print("a\\nb")'}, d)["stdout"] == "a\nb\n"
    assert run("python", {"code": "import math\ndef f(x):\n    return x * 7"}, d)["stdout"] == ""
    assert run("python", {"code": "f(math.floor(6.5))"}, d)["stdout"].strip() == "42"
    assert run("python", {"code": "import os\nos.system('echo hi')\nraise SystemExit(3)"}, d) == {"stdout": "hi\n", "stderr": "", "exit": 3}
    assert run("python", {"code": "import sys\nsys.stdout = None"}, d)["exit"] == 0 and run("python", {"code": "print(2)"}, d)["stdout"] == "2\n"
    assert "NameError" in run("python", {"code": "print(1)\nnope"}, d)["stderr"]
    assert run("python", {"code": "import time; time.sleep(30)"}, d)["exit"] == -1
    assert "NameError" in run("python", {"code": "f(1)"}, d)["stderr"]     # restarted after the timeout
    assert run("read_file", {"path": "nope.txt"}, d)["exit"] == 1
    assert run("nothing", {}, d)["exit"] == 2
    assert run("shell", {"command": "echo hi"}, d)["stdout"].strip() == "hi"
    assert run("write_file", {"path": "a.txt", "content": "x=1\n"}, d)["exit"] == 0
    assert run("write_file", {"path": "../a.txt", "content": ""}, d)["exit"] == 1
    assert run("read_file", {"path": __file__}, d)["exit"] == 1        # reads stay in the work dir, like writes
    assert run("edit_file", {"path": "a.txt", "old": "x=1", "new": "x=2"}, d)["exit"] == 0
    assert run("read_file", {"path": "a.txt"}, d)["content"] == "x=2\n"
    assert run("edit_file", {"path": "a.txt", "old": "zzz", "new": ""}, d)["exit"] == 1
    close(d)
    assert "NameError" in run("python", {"code": "math"}, d)["stderr"]
    close(d)
    print("tools ok")
