"""Verifier. Evidence first (code, no model), then the task's own >>> examples, a blind re-solve or a test written
without seeing the code. The model never sees the candidate answer, so it cannot just agree with it. Code hands
out PASS, the model only gets to veto."""
import ast
import doctest
import json
import re

from .. import tools
from ..schema import ToolCall, Verdict
from . import brief, perception

ABSTAIN = re.compile(r"don'?t know|do not know|not sure|cannot|could not|can'?t|no (reliable )?information|unknown|unable to", re.I)
NUM = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
BARE_NUM = re.compile(r"\W*-?\d[\d,]*(?:\.\d+)?\W*")
BLIND_SYS = ("Solve the task yourself from the goal and observations. answer holds only your final answer, nothing "
             "else. check is a short python script that prints the final answer when computing it is possible, "
             "otherwise null.")
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


def evidence(state):
    """Claims marked support=tool must literally match a tool output. Returns (failed ids, notes)."""
    failed, notes = [], []
    blobs = {ref: (r.get("stdout") or "") + (r.get("content") or "") for ref, r in state.tool_results.items()}
    given = set(nums(state.goal))          # operands quoted from the goal need no tool backing, results do
    for c in state.candidate.claims:
        if c.support != "tool":
            continue
        ref = next((k for k in blobs if k in (c.evidence or "")), None)   # "tool_0 shows ..." still names tool_0
        blob = blobs.get(ref)
        if blob is None:
            failed.append(c.id)
            notes.append(f"{c.id} cites {c.evidence!r} which is not a tool output")
            continue
        missing = [n for n in nums(c.text) if n not in given and n not in blob.replace(",", "")]
        if missing:
            failed.append(c.id)
            notes.append(f"{c.id}: {missing} not in {c.evidence}")
    outputs = [b for ref, b in blobs.items() if b.strip() and state.tool_results[ref].get("exit") == 0]
    answer = state.candidate.answer or ""
    if (state.task_class == "math" or BARE_NUM.fullmatch(answer)) and nums(answer) and nums(answer)[-1] not in given:
        n = nums(answer)[-1]
        if outputs and not any(n in b.replace(",", "") for b in outputs):
            failed.append("answer")
            notes.append(f"final number {n} does not appear in any tool output")
        elif not outputs and state.tool_results and state.retries == 0:
            # every python call died (usually a syntax error) and the number came from the model's head
            failed.append("answer")
            notes.append("no python call succeeded, so nothing backs the number; fix the code and run it again")
    return failed, "; ".join(notes)


def backed_by(state, text):
    """The ref of a successful python/shell/ocr run whose stdout contains text, else None."""
    t = norm(text)
    if not t:
        return None
    for ref, r in state.tool_results.items():
        if r.get("exit") == 0 and t in norm(r.get("stdout") or ""):
            return ref
    return None


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


def verify(ctx, state):
    cand = state.candidate
    answer = unfence(cand.answer)
    failed, notes = evidence(state)
    if failed:
        return Verdict(verdict="RETRY", failed_claims=failed, notes=notes)
    backed = backed_by(state, answer)
    if not ctx.is_model("verifier"):
        return Verdict(verdict="PASS", basis="evidence" if backed else "none",
                       notes=f"answer is the output of {backed}, no verifier model" if backed else "no verifier model")

    if state.task_class == "code" and _is_code(answer):
        return _verify_code(ctx, state, answer)
    if state.images and ctx.is_model("perception"):
        return _verify_vision(ctx, state, answer, backed)

    schema = {"type": "object", "additionalProperties": False, "required": ["answer", "check"],
              "properties": {"answer": {"type": "string"}, "check": {"anyOf": [tools.call_schema(["python"]), {"type": "null"}]}}}
    r = ctx.chat(state, "verifier", [{"role": "system", "content": BLIND_SYS}, {"role": "user", "content": brief(state)}],
                 schema=schema, thinking=False, max_tokens=1500)
    if r.data is None or not norm(r.data["answer"]) or ABSTAIN.search(r.data["answer"]):
        return Verdict(verdict="PASS", basis="evidence" if backed else _self_basis(cand),
                       notes=f"verifier abstained: {r.text[:100]!r}" + (f"; answer is the output of {backed}" if backed else ""))
    blind = r.data["answer"]
    if same(answer, blind):
        return Verdict(verdict="PASS", basis="consistency", notes=f"blind re-solve agrees: {blind[:200]}")
    check = r.data.get("check")
    if check and blind[:40] in json.dumps(check):
        check = None                # gemma likes print("<its own answer>"), which checks nothing
    if check and not any(v.verdict == "VERIFY_WITH_TOOL" for v in state.verdicts):
        return Verdict(verdict="VERIFY_WITH_TOOL", proposed_check=ToolCall(**check),
                       notes=f"blind re-solve got {blind[:200]!r}, checking with a tool")
    # the two disagree and there is nothing left to run: a tool output settles it, either way round
    if backed:
        return Verdict(verdict="PASS", basis="evidence",
                       notes=f"answer is the output of {backed}; the blind re-solve's {blind[:100]!r} has no tool behind it")
    ref = backed_by(state, blind)
    if ref:
        return Verdict(verdict="PASS", basis="evidence", answer=blind,
                       notes=f"the blind re-solve's answer is the output of {ref}, taking it over {answer[:100]!r}")
    if not state.tool_results and state.task_class == "qa":
        # closed-book trivia: a second small model guessing differently is not a reason to redo the work
        return Verdict(verdict="PASS", basis=_self_basis(cand),
                       notes=f"blind re-solve got {blind[:200]!r} and nothing can check either")
    return Verdict(verdict="CONFLICT", failed_claims=["answer"],
                   notes=f"blind re-solve got a different answer: {blind[:300]}")


def _self_basis(cand):
    """Closed-book answers rest on the reasoning lobe's own samples agreeing (reasoning.solve sets that) or nothing."""
    return "consistency" if cand.confidence.basis == "consistency" else "none"


def _verify_code(ctx, state, code):
    passed, attempted, err = doctest_check(ctx, code, state.goal)
    if attempted:
        if passed == attempted:
            return Verdict(verdict="PASS", basis="evidence", notes=f"{passed}/{attempted} examples from the task pass")
        return Verdict(verdict="RETRY", failed_claims=["answer"],
                       notes=f"{passed}/{attempted} examples from the task pass: " + err[-600:])
    names = defined(code)
    schema = {"type": "object", "additionalProperties": False, "required": ["test"], "properties": {"test": {"type": "string"}}}
    r = ctx.chat(state, "verifier", [{"role": "system", "content": TEST_SYS},
                                     {"role": "user", "content": brief(state) + f"\nThe solution defines: {', '.join(names) or 'nothing'}"}],
                 schema=schema, thinking=False, max_tokens=1200)
    if not r.data or not r.data["test"].strip():
        return Verdict(verdict="PASS", basis="none", notes="verifier wrote no test")
    from ..runner import run_tools
    run_tools(ctx, state, [ToolCall(name="python", args={"code": code + "\n\n" + r.data["test"]})])
    res = state.tool_results[state.observations[-1].ref]
    err = res.get("stderr") or ""
    if res.get("exit") == 0:
        return Verdict(verdict="PASS", basis="evidence", notes="blind test passed")
    if "AssertionError" not in err and _blame(err, code.count("\n") + 1) == "test":
        return Verdict(verdict="PASS", basis="none", notes="the test itself broke: " + err.strip().splitlines()[-1][:200])
    return Verdict(verdict="RETRY", failed_claims=["answer"], notes="test failed: " + err[-600:])


def _verify_vision(ctx, state, answer, backed):
    """Three readers: the perception model's description, the ocr engine's text and a second look that answers
    the question directly. Two of them agreeing is a PASS."""
    if backed:
        return Verdict(verdict="PASS", basis="evidence", notes=f"answer is in the ocr output {backed}")
    second = perception.ask(ctx, state)
    if same(answer, second):
        return Verdict(verdict="PASS", basis="consistency", notes=f"second look agrees: {second[:200]}")
    ref = backed_by(state, second)
    if ref:
        return Verdict(verdict="PASS", basis="evidence", answer=second,
                       notes=f"second look {second[:100]!r} matches the ocr output {ref}, taking it over {answer[:100]!r}")
    return Verdict(verdict="CONFLICT", failed_claims=["answer"],
                   notes=f"second look at the image got a different answer: {second[:300]}")


if __name__ == "__main__":
    assert same("The answer is 42.", "42")
    assert same("1,000", "1000.0")
    assert not same("42", "43")
    assert same("Paris", "The capital is Paris")
    assert not same("Paris", "Berlin")
    assert ABSTAIN.search("The page title could not be determined") and not ABSTAIN.search("Example Domain")
    assert _is_code("def f(x):\n    return x") and not _is_code("61") and not _is_code("Paris") and not _is_code("the answer: 61")
    assert defined("import os\ndef f(): pass\nclass C: pass") == ["f", "C"]
    goal = 'def f(x):\n    """Doubles.\n    >>> f(2)\n    4\n    >>> f(3)\n    6\n    """\n'
    assert [e.want for e in examples(goal)] == ["4\n", "6\n"]
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
