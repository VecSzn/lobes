"""Language: the final envelope. passthrough hands the value over as-is; a model rewrites it for the user
without adding facts, and code throws the rewrite away if a value went missing."""
import re

from ..schema import Confidence, Envelope, Next
from .verifier import norm, nums

SYS = ("You are the language lobe. Rewrite the draft answer for the user: same facts, same values, in the language "
       "of the goal, no new claims, no preamble. If uncertainties are listed, say so in one sentence at the end.")
HEDGE = "Not sure. Best guess: "


def faithful(out, draft, goal):
    """A rewrite may add numbers from the goal but not change or invent any, and every line of the draft (a program
    prints one value per line) must survive verbatim. gemma once rewrote a verified 97405784 into 97404784, so
    code checks, not the model."""
    given = set(nums(goal))
    if set(nums(out)) - given != set(nums(draft)) - given:
        return False
    o = norm(out)
    return all(norm(line) in o for line in draft.splitlines() if norm(line))


def confidence(state):
    return {"evidence": Confidence(score=0.9, basis="evidence"),
            "consistency": Confidence(score=0.8, basis="consistency")}.get(state.basis, Confidence(score=0.5, basis="self"))


def say(ctx, state):
    answer, unc = (state.value or "").strip(), list(state.uncertainties)
    if ctx.is_model("language") and state.route != "fast" and state.task_class != "code" and answer:
        user = f"Goal: {state.goal}\nDraft answer: {answer}\n" + ("Uncertainties: " + "; ".join(unc) if unc else "")
        schema = {"type": "object", "additionalProperties": False, "required": ["answer"], "properties": {"answer": {"type": "string"}}}
        r = ctx.chat(state, "language", [{"role": "system", "content": SYS}, {"role": "user", "content": user}],
                     schema=schema, thinking=False, max_tokens=1000)
        out = (r.data or {}).get("answer", "").strip()
        if out and faithful(out, answer, state.goal):
            answer = out
        elif out:
            ctx.trace.write("language_rejected", text=out[:500])   # the rewrite lost a value; keep the draft
    if answer and state.route != "fast" and state.task_class != "code" and state.basis == "none":
        answer = HEDGE + answer    # nothing backs it: say so where the user (and the SimpleQA judge) can see it
    return Envelope(kind="final", goal=state.goal, answer=answer, uncertainties=unc,
                    confidence=confidence(state), next=Next(action="answer"))
