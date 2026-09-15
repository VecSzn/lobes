"""Verifier: the code side of checking. Text matching for witnesses and the task's own >>> examples for code.
No model: the verifier model solved blind as a third witness until 09-15 (runner._plan says how that went)."""
import ast
import doctest
import re

from .. import tools
from ..schema import Verdict

NUM = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
PROSE = re.compile(r"^[ \t]*(\w+\(.*\))[ \t]*(?:==>|==|=>|➞|->|→)[ \t]*(.+?)[ \t]*$", re.M)
DOCTEST = '''
import doctest as _dt, sys as _sys
_ex = [_dt.Example(s, w) for s, w in {pairs!r}]
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
    a, b = norm(a) or (a or "").strip().lower(), norm(b) or (b or "").strip().lower()   # "a" is a value too
    if not a or not b:
        return False
    ta, tb = set(a.split()), set(b.split())
    if ta <= tb or tb <= ta:   # whole words: "e" is not in "wrote 71 chars"
        return True
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


def restates(program, output):
    """A check that printed its own literal computed nothing: print(391) backs no 391. -1 is not 1."""
    n = nums(output)
    return bool(n) and bool(re.search(rf"(?<![\w.-]){re.escape(n[-1])}(?![\w.])", program or ""))


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


def _literal(s):
    """A want that is a value: "an empty list" or "fibfib(n-1) + fibfib(n-2)" is prose, not an example."""
    try:
        return isinstance(ast.parse(s, mode="eval").body, (ast.Constant, ast.List, ast.Tuple, ast.Dict, ast.Set, ast.UnaryOp))
    except SyntaxError:
        return False


def examples(goal):
    """The >>> lines in the task, and without any, the prose ones: "f(2) ➞ 4", "f(2) => 4", "f(2) ==> 4",
    "f(2) == 4" (9 of the 30 HumanEval items have only those). A want that is a literal is compared as a value,
    `f(2) == want` expecting True: a docstring writes "21" where repr says '21' (HumanEval-65 failed both of its
    examples with right code). The docstring quotes go first or the last example's want ends in three quotes."""
    text = goal.replace('"""', "").replace("'''", "")
    ex = doctest.DocTestParser().get_examples(text)
    for e in ex:
        if not e.want.strip() and re.search(r"\s==\s", e.source):
            e.want = "True\n"
        elif _literal(e.want.strip()):
            e.source, e.want = f"{e.source.rstrip()} == {e.want.strip()}\n", "True\n"
    if not ex:
        ex = [doctest.Example(f"{call} == {want}\n", "True\n") for call, want in PROSE.findall(text) if _literal(want)]
    return ex


def doctest_check(ctx, code, goal):
    """Runs the task's own examples against code. -> (passed, attempted, stderr); attempted is 0 when there are none."""
    ex = examples(goal)
    if not ex:
        return 0, 0, ""
    res = tools.python(code + "\n\n" + DOCTEST.format(pairs=[(e.source, e.want) for e in ex]), ctx.workdir)
    m = re.search(r"doctest: (\d+)/(\d+)", res.get("stdout") or "")
    passed, attempted = (int(m[1]), int(m[2])) if m else (0, len(ex))
    ctx.trace.write("doctest", passed=passed, attempted=attempted, stderr=(res.get("stderr") or "")[-800:])
    return passed, attempted, res.get("stderr") or ""


def verify_code(ctx, state, code):
    """The task's own examples decide; without them the code goes out untested. A test the verifier wrote blind
    re-implemented the function in 21 of 34 tries and, with that stripped, failed right code 10 of 25 (REPORT, 17)."""
    if not _is_code(code):
        return Verdict(verdict="PASS", basis="none", notes="the answer is not code, nothing to run")
    passed, attempted, err = doctest_check(ctx, code, state.goal)
    if not attempted:
        return Verdict(verdict="PASS", basis="none", notes="no examples in the task, nothing to run")
    if passed == attempted:
        return Verdict(verdict="PASS", basis="evidence", notes=f"{passed}/{attempted} examples from the task pass")
    return Verdict(verdict="RETRY", failed_claims=["answer"],
                   notes=f"{passed}/{attempted} examples from the task pass: " + err[-600:])


if __name__ == "__main__":
    assert same("The answer is 42.", "42")
    assert same("1,000", "1000.0")
    assert not same("42", "43")
    assert same("Paris", "The capital is Paris")
    assert not same("Paris", "Berlin")
    assert _is_code("def f(x):\n    return x") and not _is_code("61") and not _is_code("Paris") and not _is_code("the answer: 61")
    goal = 'def f(x):\n    """Doubles.\n    >>> f(2)\n    4\n    >>> f(3)\n    6\n    """\n'
    assert [(e.source, e.want) for e in examples(goal)] == [("f(2) == 4\n", "True\n"), ("f(3) == 6\n", "True\n")]
    class _S:
        tool_results = {"ocr_0": {"stdout": "year 2012\nCEN TRE\nclosing down", "exit": 0}, "tool_1": {"stdout": "12", "exit": 0}}
    assert ocr_backed(_S, "12") is None and ocr_backed(_S, "2012") == "ocr_0" and ocr_backed(_S, "Closing down.") == "ocr_0"
    assert ocr_backed(_S, "centre") == "ocr_0" and ocr_backed(_S, "north") is None
    assert restates("print(391)", "391") and restates("x = 1,234\nprint(x)", "1234") is False
    assert not restates("print(17*23)", "391") and not restates("print(3910)", "391") and not restates("print(3.91)", "391")
    assert not restates("print(s[::-1])", "1") and not restates("pow(3, 100, 1000000)", "522001")
    assert same("a", "a") and not same("a", "the") and not same("e", "wrote 71 chars to s.txt") and same("new york", "new york city")

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
    goal = 'def g(a, b):\n    """Evens between.\n    g(2, 8) => [2, 4, 6, 8]\n    g(8, 2) ➞ [2, 4, 6, 8]\n    g(1, 1) == []\n    g(0, 0) -> an empty list\n    g(n) == g(n-1)\n    """\n'
    assert [e.source for e in examples(goal)] == ["g(2, 8) == [2, 4, 6, 8]\n", "g(8, 2) == [2, 4, 6, 8]\n", "g(1, 1) == []\n"]
    assert [e.source for e in examples('def c(x):\n    """\n    c(1) ==> True\n    c(2) ==> (1, -2)\n    """\n')] == ["c(1) == True\n", "c(2) == (1, -2)\n"]
    assert doctest_check(_Ctx, "def g(a, b):\n    return [x for x in range(min(a, b), max(a, b) + 1) if x % 2 == 0]", goal)[:2] == (3, 3)
    assert doctest_check(_Ctx, "def g(a, b):\n    return []", goal)[:2] == (1, 3)
    goal = 'def h(x):\n    """\n    >>> h([1, 2]) == [2, 1]\n    >>> h([])\n    []\n    >>> h("ab")\n    "ba"\n    >>> h(range(3))\n    range(2, -1, -1)\n    """\n'
    assert [e.want for e in examples(goal)] == ["True\n", "True\n", "True\n", "range(2, -1, -1)\n"]
    assert doctest_check(_Ctx, "def h(x):\n    return x[::-1]", goal)[:2] == (4, 4)
    assert doctest_check(_Ctx, "def h(x):\n    return list(x)[::-1]", goal)[:2] == (2, 4)
    print("verifier ok")
