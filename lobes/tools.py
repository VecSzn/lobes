"""Tools the motor lobe can pick. Every result is a dict that gets written to the run dir verbatim."""
import subprocess
import sys
from pathlib import Path

TIMEOUT = 10
MAX_OUT = 8000     # chars kept per stream; the rest is still on disk


def python(code: str, workdir: Path):
    try:
        p = subprocess.run([sys.executable, "-I", "-c", code], cwd=workdir, capture_output=True,
                           text=True, timeout=TIMEOUT, encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired as e:
        return {"stdout": (e.stdout or "")[-MAX_OUT:], "stderr": f"timeout after {TIMEOUT}s", "exit": -1}
    return {"stdout": p.stdout[-MAX_OUT:], "stderr": p.stderr[-MAX_OUT:], "exit": p.returncode}


def read_file(path: str, workdir: Path):
    p = Path(path)
    if not p.is_absolute():
        p = workdir / p
    try:
        return {"content": p.read_text(encoding="utf-8", errors="replace")[:MAX_OUT * 4], "exit": 0}
    except OSError as e:
        return {"stderr": str(e), "exit": 1}


TOOLS = {
    "python": (python, {"code": {"type": "string", "description": "python source, stdout is what you get back"}}),
    "read_file": (read_file, {"path": {"type": "string"}}),
}


def call_schema(only=None):
    """JSON schema for one ToolCall, name and args tied together so the grammar cannot mix them up."""
    return {"oneOf": [
        {"type": "object",
         "properties": {"name": {"const": n},
                        "args": {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}},
         "required": ["name", "args"], "additionalProperties": False}
        for n, (_, props) in TOOLS.items() if only is None or n in only]}


def run(name: str, args: dict, workdir: Path):
    if name not in TOOLS:
        return {"stderr": f"no tool named {name}", "exit": 2}
    fn, props = TOOLS[name]
    missing = [k for k in props if k not in args]
    if missing:
        return {"stderr": f"{name} needs {missing}", "exit": 2}
    return fn(**{k: args[k] for k in props}, workdir=workdir)


if __name__ == "__main__":
    import tempfile
    d = Path(tempfile.mkdtemp())
    assert run("python", {"code": "print(6*7)"}, d)["stdout"].strip() == "42"
    assert run("python", {"code": "import time; time.sleep(30)"}, d)["exit"] == -1
    assert run("read_file", {"path": "nope.txt"}, d)["exit"] == 1
    assert run("nothing", {}, d)["exit"] == 2
    print("tools ok")
