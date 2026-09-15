# Pre-registration, v4

Written 2026-09-15, after the v3 witness runs and before any v4 item is written. Not
edited once the v4 runs start; changes of mind go into REPORT.md as deviations.

v3 is `main` at commit 2d28fe9. The opponent is unchanged: the 9B on its own (condition
R, one chat call, no lobes, no tools, no images).

## Why there is a v4 at all

v3 ended with a measurement that invalidated most of its own comparisons. Medium was run
twice on the same commit and the same box and scored 272 and 266 on the 320 items the 9B
also runs; the 9B scored 268 and high scored 267. Four numbers inside a band of six. Eleven
items of 370 changed verdict between the two runs and only 224 ended on the same answer
string, because llama-server runs four items at once and batch composition moves the
floating-point reductions.

Two things follow, and they are the whole reason for this round.

- **The suites cannot separate what they are being asked to separate.** tools was 30/30
  twice and humaneval 29 and 27 out of 30; a suite at its ceiling measures nothing, and a
  30-item suite moves 3.3 points per item, which is smaller than the noise.
- **One run is not a measurement.** v3 reported a four-item lead that a second run erased.

So v4 makes the suites bigger and harder where they are saturated, and makes the repeat
run part of the protocol rather than an afterthought.

## What changes

- **tools: 30 to 60.** The existing 30 (`tools-20` to `tools-49`) keep their ids, their
  prompts and their expected values, unchanged, so every v3 number stays comparable. 30 new
  items (`tools-50` to `tools-79`) are harder: still one value each, but the value needs a
  computation long enough that no model of this size reaches it without running something.
  The two halves are reported as separate splits and also as a combined 60.
- **multistep: 30 to 60.** Same rule: `multi-10` to `multi-39` unchanged, `multi-40` to
  `multi-69` new. The new ones are 4 to 6 step chains and every one of them asks for three
  values, where the old set asked for two or three.
- **SimpleQA: kept at 30 and not run again.** Every condition is at the floor on it (the 9B
  5 of 30, this runtime 4 and 3) and it is the most expensive suite per item at high, 226
  seconds. It has no discriminative power left, but it is the only suite that measures
  abstention, which is the one place this runtime beats the 9B by a wide margin, so it stays
  in the report. The v3 numbers carry forward verbatim and are labelled as v3 numbers
  everywhere they appear. No v4 run includes it.
- **AIME added, if a pilot says it is not floored.** The set is AIME 2025, both papers, all
  30 problems, from `yentinglin/aime_2025` on HuggingFace; 2025 rather than 2024 because the
  2024 paper has been in every training set for two years. 10 to 15 items are run at medium
  before anything is frozen. If this runtime scores 0 on the pilot the suite is dropped and
  that is recorded here as a deviation, because a floored suite is the mistake this round
  exists to fix. The judge is the gsm8k judge: the answer is an integer 0 to 999.

  **The pilot ran on 2026-09-15 and AIME is in.** Medium, four workers, `5090-v4-aime-pilot`:
  16 of 30 right, 7 of the first 12, so nothing near floored. It is by far the most expensive
  suite per item, 29,826 tokens and 122 seconds against gsm8k's 5,308 and 24. Every one of the
  16 right answers had a witness program that ran, and so did 12 of the 14 wrong ones. 17 of
  the 30 answers were hedged and 4 of those were right anyway. All 30 items ran rather than
  the 10 to 15 the paragraph above asks for: the run is resumable and item by item, so
  stopping it at 12 would have thrown away work already paid for. The pilot records stay in
  `eval/results/5090-v4-aime-pilot`; they are a pilot, not one of the v4 runs, and the v4
  numbers come from the runs below with AIME in the suite list like every other suite.
- **GSM8K (200), HumanEval (30) and OCRBench (50) are unchanged**, prompts, ids and judges,
  **and they are not run again either.** Their v3 numbers carry forward the same way SimpleQA's
  do. The v3 runs and the v4 runs are the same runtime: `git diff aded056 2bb09e1 -- lobes/`
  is two docstrings in `reasoning.py` and `verifier.py` and nothing else, so a rerun would only
  draw a second sample of a distribution v3 already sampled twice at medium. Each v4 result
  file is seeded with the carried records, tagged `carried` with the run they came from, and
  `lobes eval` skips them by suite and id the way it resumes any interrupted run. What each v4
  run actually executes is 90 items: tools 50 to 79, multistep 40 to 69, and AIME.

  This was decided after the first v4 launch had already started on all six suites and was
  stopped 12 minutes in; the aborted partial results are kept as `5090-v4-aborted-fullsuite`
  and `5090-v4-high-aborted-fullsuite` on the boxes. Nothing was read off them.

Nothing about the runtime changes for these items. No prompt, no lobe, no threshold is
tuned to any suite; the new items are written against `eval/suites/make.py`, which computes
every expected value in code and never types one in, the same as the v3 items.

## How the noise is handled this time

Medium runs twice, on the same box, back to back, with nothing touched in between. R and
high run once each. The spread between the two medium runs is the band, and every claim in
REPORT.md must clear it or be written as a single draw. A gap smaller than the band is not
a finding, whichever direction it points.

## Hypotheses

Thresholds fixed now, checked after. Where a hypothesis mentions a v3 number, that number
is the first medium run.

- **V1.** On the 30 new tools items this runtime beats R by at least 10 points, and the
  gap is larger than on the old 30. This is the claim that the old tools suite was at its
  ceiling rather than the runtime being at its ceiling.
- **V2.** On the 30 new multistep items this runtime beats R by at least 10 points. Failing
  this while V1 holds would say the win is running one program, not chaining several.
- **V3.** The band between the two medium runs, on the 380 non-SimpleQA items, is at most
  8 items. Wider than that and v4 has not fixed the measurement problem either, whatever
  the scores say.
- **V4.** Tokens and seconds per item stay below R on all of tools, multistep and gsm8k,
  and the two medium runs agree within 2% on the combined total. Cost was the only thing
  that repeated in v3 and it has to keep repeating to stay a claim.
- **V5.** On the new multistep items, which ask for three values each, the share of items
  where the witnesses agree on some values and not others is higher than on the old set.
  This is a mechanism claim about the line-by-line contract, not a score claim.
- **V6.** GSM8K stays within 2 points of R, as in v3. This is the regression guard: the
  new items must not come at the cost of the suite that has enough items to say anything.
- **V7.** If AIME survives the pilot, this runtime scores at or above R on it. Thinking
  budget binds on AIME where it binds on nothing in v3, so this is the first item set where
  the effort knob has something to separate, and high is expected to beat medium here or
  the knob is useless.

## What is frozen

The item files after `python eval/suites/make.py` is run and the counts assert. The judges,
unchanged from PREREG-v3, plus the gsm8k judge on AIME. The conditions: R, D medium, D medium
again, D high. Four workers, seed 0, one box per condition, `lobes eval` resuming by suite and
id as before.

Frozen 2026-09-15 once the pilot came back: tools 60, multistep 60, gsm8k 200, humaneval 30,
ocrbench 50, AIME 30, and SimpleQA 30. Only the 90 new items per run are executed; gsm8k,
humaneval, ocrbench, SimpleQA and the old halves of tools and multistep carry over. The v4 runs
are

    lobes eval --conditions D --seeds 0 --workers 4 --suites tools,multistep,aime --tag ...

with `--effort high` for the high run and `--conditions R` for the 9B alone. SimpleQA is in no
v4 command.
