# Eval report

Run started 2026-09-14 02:47 on the machine in the README (RTX 4070 Laptop 8 GB, llama.cpp
b10951). Everything below comes from `eval/results/*.jsonl`; `lobes eval --report` prints the
tables. PREREG.md is the contract; this file says what actually happened.

## Deviations from PREREG, in the order they happened

1. `lobes eval --quick` was supposed to run seed 0 only. I forgot the flag and it ran all
   three seeds for A and part of B before I stopped it (72 items, 60 min). Those numbers are
   in `eval/results/quick-*.jsonl` and were used for the timing projection only.
2. Timing from that run: A (9B) averaged 64 s per item, 160 s on SimpleQA where the blind
   re-solve disagrees, thinking turns on and 6000 tokens run out without an answer. B (4B)
   averaged 24 s. The full plan projected to about 10 h, so the pre-registered cut rule
   applied in full: seeds 1 and 2 keep only multistep, GSM8K and SimpleQA drop to 30, B3
   is dropped. H4 (equal-compute voting) is therefore untested.
3. Item counts actually run per condition at seed 0: A 120 (no ocrbench), B/C/D/E 140; seeds 1
   and 2 add 10 multistep items each. 780 runs. F skipped, no key.

## Quick timing (seed 0-2 mixed, 3 items per suite, not part of the results)

| cond | suite     | n | correct | mean s | max s | mean tokens |
|------|-----------|---|---------|--------|-------|-------------|
| A    | gsm8k     | 9 | 8       | 26.6   | 51.8  | 2030 |
| A    | humaneval | 9 | 9       | 34.8   | 89.6  | 3561 |
| A    | tools     | 9 | 9       | 29.7   | 57.2  | 2720 |
| A    | simpleqa  | 9 | 0       | 159.5  | 455.9 | 8229 |
| A    | multistep | 9 | 5       | 70.1   | 461.1 | 4210 |
| B    | gsm8k     | 6 | 6       | 45.8   | 143.2 | 4476 |
| B    | humaneval | 6 | 6       | 17.1   | 26.6  | 2353 |
| B    | tools     | 6 | 4       | 18.8   | 38.2  | 2338 |
| B    | simpleqa  | 3 | 0       | 31.0   | 77.3  | 3398 |
| B    | ocrbench  | 3 | 2       | 10.1   | 11.7  | 1798 |
| B    | multistep | 3 | 2       | 9.8    | 10.5  | 1380 |

## Results

(filled in after the run)

## Which hypotheses held

(filled in after the run)

## Things I noticed while judging

- The multistep judge is "every expected value appears in the answer". One A item answered
  with a truncated JSON envelope (the 9B hit the token cap mid-output) and still passed
  because "210" occurred in the text. The judge is fixed by PREREG; I list such cases here
  rather than re-judge them.
- SimpleQA correctness is string containment, a lower bound. The number that matters there
  is the unsupported rate (answered and wrong).
- OCRBench gold for some items is non-Latin text; the 4B produced mojibake on one, counted
  wrong.
