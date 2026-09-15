"""One module per lobe. Each takes (ctx, state) and returns a Witness or an Envelope; which model answers is
decided by lobes.yaml, not here."""
import re
from dataclasses import dataclass

from .verifier import nums, same

FIELD = re.compile(r"\s*[,;|]\s*|\s+")


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


def lines(text):
    return [l.strip() for l in (text or "").splitlines() if l.strip()]


def _fields(v, k):
    """A witness that printed its values on one line, up to the k the other one printed."""
    if len(v) == 1 and k > 1:
        toks = [t for t in FIELD.split(v[0]) if t]
        if 1 < len(toks) <= k:
            return toks
    return v


def _same(x, y, given):
    nx = [float(n) for n in nums(x) if n not in given]
    ny = [float(n) for n in nums(y) if n not in given]
    if nx and ny:
        return abs(nx[-1] - ny[-1]) <= 1e-6 * max(1.0, abs(ny[-1]))
    return same(x, y)


def agree(a, b, goal=""):
    """A value is one line, the asked ones in order and last. Equal counts: every line must match; else the shorter
    list has to end the longer one (intermediates come first). Numbers given in the goal do not count."""
    va, vb = lines(a), lines(b)
    if not va or not vb:
        return False
    va, vb = _fields(va, len(vb)), _fields(vb, len(va))
    short, long = sorted((va, vb), key=len)
    given = set(nums(goal))
    return all(_same(x, y, given) for x, y in zip(short, long[len(long) - len(short):]))


def settle(witnesses, need, goal):
    """The first value that `need` witnesses share, with the basis a PASS on it would have. None until then.
    Once a program ran, values models wrote cannot outvote it: the sharing witnesses must include one that ran.
    The carried witness is one that ran, then the one that printed the most values."""
    live = [w for w in witnesses if w.value]
    ran = any(w.ran for w in live)
    for w in live:
        peers = [o for o in live if o is w or agree(w.value, o.value, goal)]
        if len(peers) >= need and (not ran or any(o.ran for o in peers)):
            best = max(peers, key=lambda o: (o.ran, len(lines(o.value)), -live.index(o)))
            basis = "evidence" if any(o.ran for o in peers) else "consistency" if len(peers) > 1 else "none"
            return best, basis
    return None
