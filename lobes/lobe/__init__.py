"""One module per lobe. Each takes (ctx, state) and returns an Envelope or a Verdict; which model
answers is decided by lobes.yaml, not here."""


def brief(state):
    """The shared view of the task that every lobe gets as its user message."""
    lines = [f"Goal: {state.goal}"]
    if state.plan is not None and state.plan.answer:
        lines.append(f"Plan: {state.plan.answer}")
    if state.observations:
        lines.append("Observations:")
        for o in state.observations:
            lines.append(f"[{o.ref}] ({o.source}) {o.summary}")
    if state.verdicts and state.verdicts[-1].verdict != "PASS":
        v = state.verdicts[-1]
        lines.append(f"The previous attempt was rejected ({v.verdict}): {v.notes}"
                     + (f" failed claims: {v.failed_claims}" if v.failed_claims else "")
                     + " Do it differently this time.")
    return "\n".join(lines)
