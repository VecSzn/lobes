# Pre-registration, v3

Written 2026-09-14, after the 5090 runs of v1 and v2 and before any v3 code. Not edited
after the v3 runs start; changes of mind go into REPORT.md as deviations.

v2 is `main` at commit ac5f7d0 (tag `pre-v3`). v3 is what follows it. The opponent is
the 9B on its own (condition R, one chat call, no lobes, no tools), not the 9B with the
scaffolding (A), because a small-model runtime that only beats the big model when the
big model is handicapped is not worth running.

## What the traces said

Every six-lobe version so far (v1, v2 medium, v2 high) missed the same gsm8k items
(1252, 413) and the same multistep items, and the 9B alone got them. The traces show one
mechanism, not one bug per item:

- `brief()` was a shared blackboard. The executive (a 1.2B that does not think) wrote
  the plan; the plan text, and the tool outputs the motor lobe produced by following it,
  went to every later lobe with no mark of where they came from. In 1252 the plan said
  "footballs = 20/2", the motor lobe coded it, python printed 40, and 40 became
  "evidence" for the reasoning lobe. The verifier, which does not see the candidate but
  does see the plan and the observations, wrote a check that printed 70 and put 40 in
  its answer field. The check never ran. PASS on consistency.
- The answer was regenerated as free text at every lobe instead of carried as a value.
  multi-06 had (111, 9232) in a tool output and ended with an empty string; multi-08
  handed over half of a two-part answer; the language lobe turned 168 into a sentence
  ending in 1000.
- A retry re-ran the same blackboard with "do it differently" appended. multi-04 ran
  for 30 minutes and 830k tokens that way. The verifier call alone was 41-62% of an
  item's tokens and returned an echo of the observations.

## What v3 changes

One rule replaces the blackboard: an answer is accepted when two derivations that did not
see each other agree. Everything else follows from it.

1. Witnesses. Each witness gets the goal (and, for images, the perception lobe's
   description and the ocr text, each labelled with its source) and nothing produced
   by another witness. The executive only classifies; there is no plan text anywhere.
   Which witnesses run is fixed by the task class from intake:

   | class | first | second | agree means | third, on disagreement |
   |---|---|---|---|---|
   | math, or qa that needs a tool | motor lobe writes a program, it runs | reasoning lobe answers with thinking and a check program, the program runs | last numbers equal, or normalized text match | verifier lobe (gemma, another family) solves blind with a check program, it runs |
   | code | reasoning lobe writes the code | the task's own `>>>` examples, else a test the verifier writes without seeing the code | the tests pass | a new implementation with the failure attached |
   | vision | perception lobe answers the question directly from the upscaled image | the ocr engine's text contains that answer | contained (spaces stripped) | reasoning lobe answers from the description and the ocr lines |
   | closed-book qa | reasoning lobe, three samples | | unanimous | none: hedge |

2. A program beats prose. When a witness hands over a program, what the program prints
   is that witness's value; the answer field it wrote is ignored. A program that fails
   gets one repair with its own stderr, then the witness counts as absent.
3. Compute follows disagreement, not a retry counter. Two witnesses agree: done, and
   the basis is `evidence` when one of them ran (`consistency` when neither did). They
   disagree: the third witness runs and the majority wins. No majority at the level's
   witness count: the reasoning lobe's value is returned with the "Not sure. Best
   guess:" prefix. There is no RETRY, no VERIFY_WITH_TOOL and no CONFLICT loop.
4. The value is carried. The agreed value is the answer. The language lobe may rewrite
   it for the user; the rewrite is dropped, not the value, if any number or any word not
   already in the goal goes missing. An empty answer cannot reach the user.
5. Hard caps per item, at every level: witnesses, model calls, tokens, and seconds.
   Hitting a cap ends the item with whatever the reasoning lobe produced, hedged. The
   `stuck` column in the report counts cap hits.
6. Effort levels keep the same names and one table. `n` is now the most witnesses an
   item may draw (the fixed three above, then hot samples of the reasoning lobe with
   thinking), and `retries` is the number of program repairs. `auto` still climbs
   medium, high, xhigh before the escalate model, and the escalate model, when the
   ladder is on, is one more witness, not a takeover.

The roster does not change (one family per lobe, nothing above 4B, runs on the 4070).
A slot is swapped only if a trace shows the model, not the contract, is the limit.

## Suites

Same sources and shuffle seed as before; the first items of every enlarged suite are the
ones already run, so old and new results overlap on them.

| suite | n | change |
|---|---|---|
| gsm8k | 200 | was 30; the 30 are the first 30 of the 200 |
| humaneval | 30 | unchanged |
| tools | 30 | new items tools-20..49; the old 20 stay in history (tag `pre-v3`) |
| multistep | 30 | new items multi-10..39; the old 10 likewise |
| simpleqa | 30 | unchanged |
| ocrbench | 50 | was 20; the 20 are the first 20 of the 50 |

The new tools and multistep items are written by me with the answers computed by a
python script (`eval/suites/make.py`), not by hand and not by a model. They are longer
chains than the old ones (three to five dependent steps, big integers, dates, hashes,
file round trips) so that a model computing in its head has room to slip.

Judges: gsm8k, humaneval, simpleqa, ocrbench and tools as in PREREG. multistep is
loosened on numbers only: a numeric expected value is correct when any number in the
answer equals it (commas stripped, 1e-6 relative), because the 9B writes "1,234" and the
old `norm()` containment read that as "1 234". String expected values are normalized
containment as before. The multistep run is seed 0 only; three seeds of ten items told
less than one seed of thirty.

## Conditions and runs

| tag | code | condition | effort | suites |
|---|---|---|---|---|
| `5090-v3` | v3 | R | (one call) | all but ocrbench |
| `5090-pre-v3` | `pre-v3` | D | medium | all |
| `5090-v3` | v3 | D | medium | all |
| `5090-v3-high` | v3 | D | high | all |

R and D-medium at `pre-v3` are the two comparison lines; humaneval and simpleqa are
re-run for them under the new tags even though the items did not change, so that every
table comes from one directory. Seed 0 throughout. One RTX 5090 32 GB per tag as before,
executive on the GPU, all models resident (no swaps), so tokens and seconds compare
across tags. If time runs short the order of cuts is: D high first, then ocrbench to 20,
then gsm8k to 100 for every line at once.

## Hypotheses

W1. multistep and tools: v3 D medium beats R by at least 10 points on each (3 of 30),
    and among the items where exactly one of the two is right, v3 is the right one more
    often than not.
W2. gsm8k (200): v3 D medium is not below R minus 2 points, and the items the old
    versions all missed (1252, 413) are right. Winning outright is the hope, not the
    hypothesis: the 9B alone is at 29/30 on the first 30 and the 4B has less room.
W3. Cost: mean tokens per item and mean seconds per item of v3 D medium are below R on
    gsm8k, multistep, tools and simpleqa, on the same 5090. Also below v2 D high
    (`5090-v2-high`) on every suite.
W4. Caps: no item exceeds the medium caps (they end it), and at most 2% of items hit one.
    The longest v3 item is under 60 s at medium.
W5. Agreement is a calibrated signal: on gsm8k, items where the first two witnesses
    agreed are right at least 97% of the time, and the disagreement rate is under 25%.
    If agreed-and-wrong is common, the witnesses are not independent and the design is
    wrong, whatever the headline accuracy says.
W6. ocrbench (50): v3 D is at or above `pre-v3` D and above the single 4B (B, first 20
    items only, from `5090-v2`) by 5 points on the overlap.
W7. simpleqa: unsupported (answered, not abstained, wrong) is not above `pre-v3` D, and
    confident correct is not below it by more than one item. This suite is a hedge test,
    not an accuracy test, for every small-model line.
W8. humaneval: v3 D medium within 2 items of v2 D high (26/30); the code path is the one
    part of v2 that is kept.

A hypothesis that fails is reported as failed. If W5 fails the report says the mechanism
is wrong, not just the number.

## Recorded per item, beyond the v2 fields

`witnesses` (how many ran), `agreed` (a majority formed), `disagree` (the first two
differed), `capped` (which cap ended the item, if any), `basis` and `passed` as before,
`level` for auto.
