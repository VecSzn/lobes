"""Verifier: the code side of checking. Text matching for witnesses, the task's own >>> examples for code, and a
test written without seeing the code when there are none. The verifier model also acts as a blind witness on
computable tasks; that runs through reasoning.witness with lobe="verifier"."""
import ast
import doctest
import re

from .. import tools
from ..schema import ToolCall, Verdict

NUM = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
TEST_SYS = ("You get a task and the names the solution defines; you do not get the code. Write a short python test "
            "that calls those names with a few inputs and asserts the expected results. Define nothing else. No "
            "third-party imports.")
DOCTEST = '''
import doctest as _dt, sys as _sys
_ex = _dt.DocTestParser().get_examples({goal!r})
_r = _dt.DocTestRunner(optionflags=_dt.NORMALIZE_WHITESPACE | _dt.ELLIPSIS).run(
    _dt.DocTest(_ex, globals(), "goal", None, 0, None), out=_sys.stderr.write)
print(f"doctest: {{_r.attempted - _r.failed}}/{{_r.attempted}} examples passed")
_sys.exit(_r.failed > 0)
'''


def nums(s):
    return [n.replace(",", "") for n in NUM.findall(s or "")]


def norm(s):
    s = re.sub(r"[^\w\s]", " ", (s or "").lower())
    return " ".join(w for w in s.split() if w not in ("the", "a", "an", "is", "are", "was", "of"))


def same(a, b):
    na, nb = nums(a), nums(b)
    if na and nb:
        x, y = float(na[-1]), float(nb[-1])
        return abs(x - y) <= 1e-6 * max(1.0, abs(y))
    a, b = norm(a), norm(b)
    if not a or not b:
        return False
    if a == b or a in b or b in a:
        return True
    ta, tb = set(a.split()), set(b.split())
    return len(ta & tb) / len(ta | tb) >= 0.5


def ocr_backed(state, text):
    """The ocr run whose text contains this value, else None. A short single token has to be a whole word out
    there ("12" is not backed by "2012"); longer ones may span the engine's word breaks."""
    t = norm(text)
    if not t:
        return None
    for ref, r in state.tool_results.items():
        out = norm(r.get("stdout") or "") if ref.startswith("ocr_") else ""
        if not out:
            continue
        if " " not in t and len(t) < 4:
            if t in out.split():
                return ref
        elif t.replace(" ", "") in out.replace(" ", ""):
            return ref
    return None


def restates(program, answer):
    """A check that carries its own answer as a literal computes nothing: print(391) backs no 391."""
    n = nums(answer)
    return bool(n) and bool(re.search(rf"(?<![\w.]){re.escape(n[-1])}(?![\w.])", program or ""))


def unfence(src):
    return re.sub(r"^\s*```\w*\n|\n```\s*$", "", (src or "").rstrip())


def _is_code(src):
    """Prose on a task misfiled as code ("61", "Paris") compiles too; ask for a statement that does something."""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return False
    return any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Import, ast.ImportFrom,
                              ast.Assign, ast.For, ast.While, ast.If, ast.With, ast.Return)) for n in ast.walk(tree))


def defined(src):
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    return [n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]


def examples(goal):
    """The >>> lines in the task. The docstring quotes go first or the last example's want ends in three quotes."""
    return doctest.DocTestParser().get_examples(goal.replace('"""', "").replace("'''", ""))


def doctest_check(ctx, code, goal):
    """Runs the task's own examples against code. -> (passed, attempted, stderr); attempted is 0 when there are none."""
    ex = examples(goal)
    if not ex:
        return 0, 0, ""
    res = tools.python(code + "\n\n" + DOCTEST.format(goal=goal.replace('"""', "").replace("'''", "")), ctx.workdir)
    m = re.search(r"doctest: (\d+)/(\d+)", res.get("stdout") or "")
    passed, attempted = (int(m[1]), int(m[2])) if m else (0, len(ex))
    ctx.trace.write("doctest", passed=passed, attempted=attempted, stderr=(res.get("stderr") or "")[-800:])
    return passed, attempted, res.get("stderr") or ""


def _blame(stderr, code_lines):
    """Which side of the candidate/test seam raised, by the last <string> frame's line number."""
    hits = re.findall(r'File "<string>", line (\d+)', stderr)
    return "candidate" if hits and int(hits[-1]) <= code_lines else "test"


def verify_code(ctx, state, code):
    """The task's own examples decide when there are any; else a test the verifier writes blind. Code hands out
    PASS, the model only gets to veto."""
    if not _is_code(code):
        return Verdict(verdict="PASS", basis="none", notes="the answer is not code, nothing to run")
    passed, attempted, err = doctest_check(ctx, code, state.goal)
    if attempted:
        if passed == attempted:
            return Verdict(verdict="PASS", basis="evidence", notes=f"{passed}/{attempted} examples from the task pass")
        return Verdict(verdict="RETRY", failed_claims=["answer"],
                       notes=f"{passed}/{attempted} examples from the task pass: " + err[-600:])
    if not ctx.is_model("verifier"):
        return Verdict(verdict="PASS", basis="none", notes="no examples and no verifier model")
    from . import brief
    names = defined(code)
    schema = {"type": "object", "additionalProperties": False, "required": ["test"], "properties": {"test": {"type": "string"}}}
    r = ctx.chat(state, "verifier", [{"role": "system", "content": TEST_SYS},
                                     {"role": "user", "content": brief(state) + f"\nThe solution defines: {', '.join(names) or 'nothing'}"}],
                 schema=schema, thinking=False, max_tokens=1200)
    if not r.data or not r.data["test"].strip():
        return Verdict(verdict="PASS", basis="none", notes="verifier wrote no test")
    from ..runner import run_tool
    _, res, _ = run_tool(ctx, state, ToolCall(name="python", args={"code": code + "\n\n" + r.data["test"]}))
    err = res.get("stderr") or ""
    if res.get("exit") == 0:
        return Verdict(verdict="PASS", basis="evidence", notes="blind test passed")
    if "AssertionError" not in err and _blame(err, code.count("\n") + 1) == "test":
        return Verdict(verdict="PASS", basis="none", notes="the test itself broke: " + err.strip().splitlines()[-1][:200])
    return Verdict(verdict="RETRY", failed_claims=["answer"], notes="test failed: " + err[-600:])


if __name__ == "__main__":
    assert same("The answer is 42.", "42")
    assert same("1,000", "1000.0")
    assert not same("42", "43")
    assert same("Paris", "The capital is Paris")
    assert not same("Paris", "Berlin")
    assert _is_code("def f(x):\n    return x") and not _is_code("61") and not _is_code("Paris") and not _is_code("the answer: 61")
    assert defined("import os\ndef f(): pass\nclass C: pass") == ["f", "C"]
    goal = 'def f(x):\n    """Doubles.\n    >>> f(2)\n    4\n    >>> f(3)\n    6\n    """\n'
    assert [e.want for e in examples(goal)] == ["4\n", "6\n"]
    class _S:
        tool_results = {"ocr_0": {"stdout": "year 2012\nCEN TRE\nclosing down", "exit": 0}, "tool_1": {"stdout": "12", "exit": 0}}
    assert ocr_backed(_S, "12") is None and ocr_backed(_S, "2012") == "ocr_0" and ocr_backed(_S, "Closing down.") == "ocr_0"
    assert ocr_backed(_S, "centre") == "ocr_0" and ocr_backed(_S, "north") is None
    assert restates("print(391)", "391") and restates("x = 1,234\nprint(x)", "1234") is False
    assert not restates("print(17*23)", "391") and not restates("print(3910)", "391") and not restates("print(3.91)", "391")
    assert _blame('File "<string>", line 2, in <module>\nNameError', 5) == "candidate"
    assert _blame('File "<string>", line 9, in <module>\nNameError', 5) == "test"

    class _Ctx:
        from pathlib import Path
        import tempfile
        workdir = Path(tempfile.mkdtemp())

        class trace:
            @staticmethod
            def write(*a, **k): pass
    assert doctest_check(_Ctx, "def f(x):\n    return 2 * x", goal)[:2] == (2, 2)
    assert doctest_check(_Ctx, "def f(x):\n    return 4 if x == 2 else 0", goal)[:2] == (1, 2)
    assert doctest_check(_Ctx, "def f(x)\n    return x", goal)[:2] == (0, 2)
    assert doctest_check(_Ctx, "x = 1", "no examples here") == (0, 0, "")
    print("verifier ok")
