"""Verifier. Evidence first (code, no model), then a blind re-solve or a generated test. The model never
sees the candidate answer on qa/math tasks, so it cannot just agree with it. Code hands out PASS, the model only
gets to veto."""
import re

from .. import tools
from ..schema import ToolCall, Verdict
from . import brief

NUM = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
BLIND_SYS = ("Solve the task yourself from the goal and observations. answer holds only your final answer, nothing "
             "else. check is a short python script that prints the final answer when computing it is possible, "
             "otherwise null.")
TEST_SYS = ("You get a task and the code written for it. Write a short python test: call the code with a few inputs "
            "and assert the expected results. Do not repeat the code itself, it is prepended for you. No imports of "
            "third-party packages.")


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
        blob = blobs.get(c.evidence or "")
        if blob is None:
            failed.append(c.id)
            notes.append(f"{c.id} cites {c.evidence!r} which is not a tool output")
            continue
        missing = [n for n in nums(c.text) if n not in given and n not in blob.replace(",", "")]
        if missing:
            failed.append(c.id)
            notes.append(f"{c.id}: {missing} not in {c.evidence}")
    outputs = [b for b in blobs.values() if b.strip()]     # a tool that printed nothing is not evidence against
    if state.task_class == "math" and outputs and nums(state.candidate.answer or ""):
        n = nums(state.candidate.answer)[-1]
        if not any(n in b.replace(",", "") for b in outputs):
            failed.append("answer")
            notes.append(f"final number {n} does not appear in any tool output")
    return failed, "; ".join(notes)


def _compiles(src):
    try:
        compile(src, "<candidate>", "exec")
        return True
    except SyntaxError:
        return False       # a prose answer on a task misfiled as code goes to the blind re-solve instead


def verify(ctx, state):
    cand = state.candidate
    failed, notes = evidence(state)
    if failed:
        return Verdict(verdict="RETRY", failed_claims=failed, notes=notes)
    if not ctx.is_model("verifier"):
        return Verdict(verdict="PASS", basis="evidence" if state.tool_results else "none",
                       notes="no verifier model" if not state.tool_results else "tool-backed, no verifier model")

    if state.task_class == "code" and _compiles(cand.answer or ""):
        schema = {"type": "object", "additionalProperties": False, "required": ["test"], "properties": {"test": {"type": "string"}}}
        r = ctx.chat(state, "verifier", [{"role": "system", "content": TEST_SYS},
                                         {"role": "user", "content": brief(state, with_candidate=True)}],
                     schema=schema, thinking=False, max_tokens=1200)
        if not r.data or not r.data["test"].strip():
            return Verdict(verdict="PASS", basis="none", notes="verifier wrote no test")
        from ..runner import run_tools
        check = ToolCall(name="python", args={"code": (cand.answer or "") + "\n\n" + r.data["test"]})
        run_tools(ctx, state, [check])
        res = state.tool_results[state.observations[-1].ref]
        if res.get("exit") == 0:
            return Verdict(verdict="PASS", basis="evidence", notes="generated test passed")
        return Verdict(verdict="RETRY", failed_claims=["answer"], notes="test failed: " + (res.get("stderr") or "")[-600:])

    schema = {"type": "object", "additionalProperties": False, "required": ["answer", "check"],
              "properties": {"answer": {"type": "string"}, "check": {"anyOf": [tools.call_schema(["python"]), {"type": "null"}]}}}
    r = ctx.chat(state, "verifier", [{"role": "system", "content": BLIND_SYS}, {"role": "user", "content": brief(state)}],
                 schema=schema, thinking=False, max_tokens=1500)
    if r.data is None or not norm(r.data["answer"]):
        return Verdict(verdict="PASS", basis="evidence" if state.tool_results else "none",
                       notes=f"verifier abstained: {r.text[:100]!r}")
    if same(cand.answer, r.data["answer"]):
        return Verdict(verdict="PASS", basis="consistency", notes=f"blind re-solve agrees: {r.data['answer'][:200]}")
    check = r.data.get("check")
    if check and not any(v.verdict == "VERIFY_WITH_TOOL" for v in state.verdicts):
        return Verdict(verdict="VERIFY_WITH_TOOL", proposed_check=ToolCall(**check),
                       notes=f"blind re-solve got {r.data['answer'][:200]!r}, checking with a tool")
    return Verdict(verdict="CONFLICT", failed_claims=["answer"],
                   notes=f"blind re-solve got a different answer: {r.data['answer'][:300]}")


if __name__ == "__main__":
    assert same("The answer is 42.", "42")
    assert same("1,000", "1000.0")
    assert not same("42", "43")
    assert same("Paris", "The capital is Paris")
    assert not same("Paris", "Berlin")
    print("verifier ok")
