"""Language: the final envelope. passthrough hands the candidate over as-is; a model rewrites it for the user
without adding facts."""
from ..schema import Confidence, Envelope, Next
from .verifier import norm, nums

SYS = ("You are the language lobe. Rewrite the draft answer for the user: same facts, same numbers, in the language "
       "of the goal, no new claims, no preamble. If uncertainties are listed, say so in one sentence at the end.")


def faithful(out, draft, goal):
    """A rewrite may add numbers from the goal but not change or invent any; a short draft must survive verbatim.
    gemma once rewrote a verified 97405784 into 97404784, so code checks, not the model."""
    given = set(nums(goal))
    if set(nums(out)) - given != set(nums(draft)) - given:
        return False
    return len(draft.split()) > 6 or norm(draft) in norm(out)


def confidence(state):
    if not state.verdicts:
        return Confidence(score=0.5, basis="self")
    v = state.verdicts[-1]
    if v.verdict != "PASS":
        return Confidence(score=0.2, basis="self")
    return {"evidence": Confidence(score=0.9, basis="evidence"),
            "consistency": Confidence(score=0.8, basis="consistency")}.get(v.basis, Confidence(score=0.5, basis="self"))


def say(ctx, state):
    cand = state.candidate
    unc = list(cand.uncertainties)
    if state.verdicts and state.verdicts[-1].verdict != "PASS":
        unc.append("verification did not pass: " + state.verdicts[-1].notes[:300])
    answer = cand.answer or ""
    if ctx.is_model("language") and state.route != "fast" and state.task_class != "code":
        user = f"Goal: {state.goal}\nDraft answer: {answer}\n" + \
            ("Claims:\n" + "\n".join(f"- {c.text} ({c.support})" for c in cand.claims) + "\n" if cand.claims else "") + \
            ("Uncertainties: " + "; ".join(unc) if unc else "")
        schema = {"type": "object", "additionalProperties": False, "required": ["answer"], "properties": {"answer": {"type": "string"}}}
        r = ctx.chat(state, "language", [{"role": "system", "content": SYS}, {"role": "user", "content": user}],
                     schema=schema, thinking=False, max_tokens=1000)
        out = (r.data or {}).get("answer", "").strip()
        if out and faithful(out, answer, state.goal):
            answer = out
        elif out:
            ctx.trace.write("language_rejected", text=out[:500])   # the rewrite changed the facts; keep the draft
    return Envelope(kind="final", goal=state.goal, claims=cand.claims, answer=answer, uncertainties=unc,
                    confidence=confidence(state), next=Next(action="answer"))
