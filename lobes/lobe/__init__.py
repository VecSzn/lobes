"""One module per lobe. Each takes (ctx, state) and returns a Witness or an Envelope; which model answers is
decided by lobes.yaml, not here."""
from dataclasses import dataclass

from .verifier import nums, same


@dataclass
class Witness:
    """One derivation of the answer. ran: the value is what a program printed, not what a model wrote."""
    lobe: str
    value: str | None
    ran: bool = False
    ref: str | None = None        # tool_N of the run that printed it
    note: str = ""


def brief(state):
    """What a witness gets: the goal, and for images what the perception lobe and the ocr engine read, each with
    its source. Never another witness's plan, program or output."""
    lines = [f"Goal: {state.goal}"]
    for o in state.observations:
        lines.append(f"[{o.ref}] ({o.source}) {o.summary}")
    if state.feedback:
        lines.append(f"The previous attempt was rejected: {state.feedback} Do it differently this time.")
    return "\n".join(lines)


def agree(a, b, goal=""):
    """Two witnesses agree when their last result numbers match, or the sets of them do, else on the text."""
    if not a or not b:
        return False
    given = set(nums(goal))
    ra = [float(n) for n in nums(a) if n not in given]
    rb = [float(n) for n in nums(b) if n not in given]
    if ra and rb:
        return abs(ra[-1] - rb[-1]) <= 1e-6 * max(1.0, abs(rb[-1])) or set(ra) == set(rb)
    return same(a, b)


def settle(witnesses, need, goal):
    """The first value that `need` witnesses share, with the basis a PASS on it would have. None until then.
    The carried witness is one that ran, then the one that printed the most values."""
    live = [w for w in witnesses if w.value]
    for w in live:
        peers = [o for o in live if o is w or agree(w.value, o.value, goal)]
        if len(peers) >= need:
            best = max(peers, key=lambda o: (o.ran, len(nums(o.value)), -live.index(o)))
            basis = "evidence" if any(o.ran for o in peers) else "consistency" if len(peers) > 1 else "none"
            return best, basis
    return None
