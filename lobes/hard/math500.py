r"""MATH-500 (HuggingFaceH4/MATH-500), all 500 problems.

Judged the way published MATH-500 numbers are: take the last \boxed{} out of the answer and ask
whether it is mathematically equal to the gold, not whether it is the same string. The equality is
math_verify's (HuggingFace), which is latex2sympy2_extended plus sympy underneath.
"""
import json
import logging
import re

from math_verify import parse, verify

from lobes import config

# math_verify's timeouts spawn a process per call on Windows; short latex parses in milliseconds.
# ponytail: no timeout, add a thread-based one if a model answer ever hangs sympy.
logging.getLogger("math_verify").setLevel(logging.ERROR)      # it warns once per process about that
TAIL = "\n\nPlease reason step by step, and put your final answer within \\boxed{}."
_BOX = re.compile(r"\\boxed\s*{|\\fbox\s*{")
_SPACING = re.compile(r"\\[!,;:>]|\\qquad|\\quad|\\ |~")
_WORDS = re.compile(r"\\(?:text|mbox|textbf|textit|mathrm|mathbf|mathit)\s*\{([^{}]*)\}")


def _boxed(text):
    r"""Contents of the last balanced \boxed{...}. A truncated one falls back to the one before it."""
    for start in reversed([m.end() for m in _BOX.finditer(text)]):
        depth, j = 1, start
        while j < len(text) and depth:
            depth += (text[j] == "{") - (text[j] == "}")
            j += 1
        if not depth:
            return text[start:j - 1].strip()
    m = re.findall(r"\\boxed\s+([^\s{}]+)", text)       # \boxed 5, written without braces
    return m[-1] if m else ""


def _clean(s):
    r"""Strip latex decoration, keeping the expression parseable. Both sides go through this."""
    s = (s or "").strip()
    s = re.sub(r"^\$+|\$+$", "", s).strip()
    s = re.sub(r"\\left\.|\\right\.", "", s)            # before \left/\right, or the dot is left behind
    s = s.replace("\\left", "").replace("\\right", "")
    s = _SPACING.sub("", s)
    s = re.sub(r"\\[dt]frac", r"\\frac", s)
    s = re.sub(r"\^\s*\{?\s*\\circ\s*\}?", "", s)       # degrees
    s = s.replace("\\$", "").replace("\\%", "").replace("%", "")
    s = re.sub(r"\\\.$", "", s)                         # before the trailing dot goes, or a lone \ is left
    s = re.sub(r"[.\s]+$", "", s).strip()
    # digit groups of three only, so the tuple "2,3" and the value list "3,5,7" keep their commas
    if re.fullmatch(r"-?\d{1,3}(?:,\s*\d{3})+(?:\.\d+)?", s):
        s = re.sub(r",\s*", "", s)
    if s.count("=") == 1:
        lhs, rhs = s.split("=")
        if len(lhs.strip()) <= 2:                       # "x = 5" answers the question with 5.
            s = rhs.strip()                             # a whole equation like "5x-7y+4=0" is the answer itself
    return s


def _norm(s):
    """Comparison key for when sympy cannot read one of the sides (choice letters, prose, units)."""
    return re.sub(r"\s+", "", _WORDS.sub(r"\1", _clean(s)))


def load(data_dir):
    """All 500 problems. data_dir holds math500_test.jsonl."""
    lines = (data_dir / "math500_test.jsonl").read_text(encoding="utf-8").split("\n")
    rows = [json.loads(l) for l in lines if l.strip()]
    return [{"id": f"math500-{i}", "prompt": r["problem"] + TAIL, "gold": r["answer"],
             "subject": r["subject"], "level": r["level"], "unique_id": r["unique_id"]}
            for i, r in enumerate(rows)]


def judge(item, answer):
    """-> (correct, abstained). Never abstains: every item has one right answer."""
    text = (answer or "").strip()
    gold, pred = _clean(item["gold"]), _clean(_boxed(text))
    if pred and _norm(pred) == _norm(gold):
        return True, False
    try:
        g = parse("\\boxed{%s}" % gold, parsing_timeout=None)
        # no box: hand math_verify the raw text, which is the extraction lighteval scores MATH-500 with.
        # it reads a bare number or a $...$ expression, and gives up on unanchored latex.
        p = parse("\\boxed{%s}" % pred if pred else text, parsing_timeout=None)
        return bool(g) and bool(p) and bool(verify(g, p, timeout_seconds=None)), False
    except Exception:
        return False, False


if __name__ == "__main__":
    import time

    items = load(config.ROOT / "eval" / "data")
    assert len(items) == 500, len(items)
    print(f"loaded {len(items)} items, ids {items[0]['id']}..{items[-1]['id']}")

    t0 = time.perf_counter()
    bad = [it["id"] for it in items if not judge(it, "\\boxed{%s}" % it["gold"])[0]]
    print(f"1. gold fed back as the answer: {len(items) - len(bad)}/{len(items)}"
          f"  ({time.perf_counter() - t0:.1f}s)")
    if bad:
        print("   failed:", " ".join(bad))
        for i in bad:
            print("     ", i, repr(next(it["gold"] for it in items if it["id"] == i)))

    # that pass rate alone proves little: an identical string short-circuits before sympy is asked.
    # this is the number that matters, how many golds sympy can actually read.
    unreadable = []
    for it in items:
        try:
            g = parse("\\boxed{%s}" % _clean(it["gold"]), parsing_timeout=None)
            ok = bool(g) and bool(verify(g, g, timeout_seconds=None))
        except Exception:
            ok = False
        if not ok:
            unreadable.append(it)
    print(f"   of those, sympy reads and self-verifies {len(items) - len(unreadable)}/{len(items)};"
          f" {len(unreadable)} only match as normalized strings:")
    for it in unreadable:
        print("     ", it["id"], repr(it["gold"]))

    hits = [it["id"] for it in items if judge(it, "\\boxed{0}")[0]]
    # the raw gold string, not _norm: a normalization bug that flattens some gold to 0 must not hide here
    zeros = [it["id"] for it in items if it["gold"].strip() == "0"]
    print(f"2. every answer replaced by 0: {len(hits)}/{len(items)} pass, "
          f"{len(zeros)} of which are the items whose gold is literally 0 ({' '.join(zeros)})")
    assert set(hits) == set(zeros), (set(hits) ^ set(zeros))

    print("3. empty answer:", judge(items[0], "")[0], judge(items[0], None)[0],
          judge(items[0], "I don't know")[0])
    assert not any(judge(items[0], a)[0] for a in ("", None, "I don't know"))

    cases = [
        (r"\frac{1}{2}", r"\boxed{0.5}", True),
        (r"\frac{1}{2}", r"\boxed{1/2}", True),
        (r"\frac{1}{2}", r"\boxed{\frac{1}{3}}", False),
        (r"\left( 3, \frac{\pi}{2} \right)", r"\boxed{(3, \pi/2)}", True),
        (r"\left( 3, \frac{\pi}{2} \right)", r"\boxed{(3, \pi/3)}", False),
        (r"2\sqrt{3}", r"\boxed{2 \sqrt 3}", True),
        (r"2\sqrt{3}", r"\boxed{3\sqrt{2}}", False),
        (r"5", r"so \boxed{x = 5} follows", True),
        (r"\text{(E)}", r"\boxed{\text{(E)}}", True),
        (r"\text{(E)}", r"\boxed{\text{(B)}}", False),
        (r"2\text{ cm}", r"\boxed{2}", True),
        (r"14", r"\boxed{\frac{28}{2}\!}", True),
        (r"\frac{14}{3}", r"\boxed{4.666667}", True),     # math_verify compares floats at 6 decimals
        (r"\frac{14}{3}", r"\boxed{4.7}", False),
        (r"90^\circ", r"\boxed{90}", True),
        (r"\$25", r"\boxed{25}", True),
        (r"3", r"first \boxed{9}, on reflection \boxed{3}", True),   # the last box wins
        (r"3", r"\boxed{3", False),                                  # truncated, no balanced box
        (r"25", r"\boxed{25\.}", True),
        (r"25", r"\boxed{25 }  ", True),
        (r"5x - 7y + 11z + 4 = 0", r"\boxed{0}", False),             # the equation is the answer, not its rhs
        (r"5x - 7y + 11z + 4 = 0", r"\boxed{5x-7y+11z+4=0}", True),
        (r"\text{Evelyn}", r"\boxed{\text{Evelyn}}", True),
        (r"\frac{270}7\text{ degrees}", r"\boxed{\frac{270}{7}}", True),
        (r"42", "so x = 42", True),                                  # no box, a bare number still reads
        (r"(3, \pi/2)", "The answer is (3, \\pi/2).", False),        # no box, unanchored latex does not
        (r"11,\! 111,\! 111,\! 100", r"\boxed{11111111100}", True),  # thousands separators, math500-217
        (r"58,500", r"\boxed{58500}", True),
        (r"2,3", r"\boxed{23}", False),                              # a two-value list is not twenty-three
        (r"2,3", r"\boxed{3,2}", True),                              # but "enter all values" ignores order
        (r"(6,31,-1)", r"\boxed{(-1,31,6)}", False),                 # a coordinate triple does not
    ]
    for gold, answer, want in cases:
        got = judge({"gold": gold}, answer)[0]
        assert got == want, (gold, answer, "got", got, "want", want)
    print(f"4. {len(cases)} hand cases: ok")

    # the strongest cheap check: every item judged against its neighbour's gold, so sympy runs on all 500
    t0 = time.perf_counter()
    cross = [(it["id"], it["gold"], items[(k + 1) % len(items)]["gold"]) for k, it in enumerate(items)
             if judge(it, "\\boxed{%s}" % items[(k + 1) % len(items)]["gold"])[0]]
    print(f"5. each item judged against the next item's gold: {len(cross)}/{len(items)} pass"
          f"  ({time.perf_counter() - t0:.1f}s)")
    for i, g, other in cross:
        print(f"      {i}  gold {g!r}  accepted {other!r}")
