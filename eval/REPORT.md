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
4. 5090 run: the executive (LFM2.5-1.2B) sat on the CPU as on the 4070 for v1 A and the first
   124 items of D s0. The pod shows 384 threads but its cgroup allows 40, so llama.cpp spent
   4.3 s per call fighting itself, 36% of a D item. From D s0 item 125 on it runs on the GPU,
   0.27 s per call. Seconds for v1 D are therefore mixed; tokens and accuracy are not affected.
5. Found while reading the 5090 E traces: the eval passes one fixed seed to every model call, and
   llama.cpp then returns the same text for every sample at the same temperature. Every vote in
   v1 was three identical samples (131 of 131 votes on the 4070, 108 of 110 on the 5090), so
   "three samples and a vote" was one sample. The v1 numbers stand as run. The fix (seed +
   call index, commit 6c2a145) went into both v2 and v3 before their 5090 runs; the 44 v2
   items already run without it were discarded and rerun.
6. Condition R was added after the 5090 runs started: the 9B as shipped, one chat call per
   item at the runner's cold-sample temperature and seed, thinking at the template default,
   12000-token cap, no tools, no images (so no ocrbench). The prompt gets one extra line asking
   for the answer alone on the last line, since the judges read free text; on humaneval the last
   fenced block is what gets run. It is the baseline the other conditions should be read against.
7. Forced answer, added after a quick R run on the 4070: with thinking at the template default,
   4 of the first 8 items spent the whole 12000-token cap inside the think block and came back
   empty. Now when a call stops on the length limit with reasoning and no content, the same
   request goes out once more with that reasoning closed by a "time is up" line and the answer
   prefilled, 2500 tokens, so the model answers from what it had. Every thinking call gets this,
   framework and R alike. It went in after v2 A/D/E and after B/C had started, so v2 runs without
   it and v3 and R run with it; the tables count how often it fired, and a v3 D medium run
   (medium is otherwise unchanged from v2) measures its effect on its own.
8. The v2 A/D/E run died after 248 items, on D s0 simpleqa-1404: the answer carried a U+2028
   line separator, Python's splitlines() breaks on that, and reading the trace back failed.
   Fixed by splitting on newlines only; D/E resumed from where they stopped, after B/C, which
   had started in the meantime.

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

### 4070, v1 code (tag `v1-4070`), seed 0

B and C were still running when this section was first written; their columns are
filled in below as they land. Accuracy %:

| suite     | A (9B) | D (specialists) | E (D + 9B rung) |
|-----------|--------|-----------------|-----------------|
| gsm8k     | 90     | 90              | 90              |
| humaneval | 90     | 63              | 63              |
| tools     | 90     | 80              | 85              |
| simpleqa  | 3      | 3               | 3               |
| ocrbench  | –      | 45              | 45              |
| multistep | 60     | 90              | 90              |

Cost, seed 0 (tokens and seconds are means per item; swaps are model loads per item):

| suite     | A tok | D tok | E tok | A s  | D s | E s  | D swaps | E swaps |
|-----------|-------|-------|-------|------|-----|------|---------|---------|
| gsm8k     | 3710  | 4002  | 4799  | 50   | 38  | 50   | 2.0     | 2.4     |
| humaneval | 5227  | 4054  | 4054  | 55   | 33  | 32   | 1.6     | 1.6     |
| tools     | 2804  | 3547  | 4360  | 40   | 30  | 39   | 2.0     | 2.8     |
| simpleqa  | 9596  | 7079  | 10679 | 179  | 73  | 135  | 1.9     | 4.1     |
| ocrbench  | –     | 5315  | 7389  | –    | 39  | 57   | 2.9     | 3.5     |
| multistep | 5181  | 4480  | 5443  | 83   | 38  | 55   | 2.0     | 2.4     |

multistep across seeds (accuracy %, s0 / s1 / s2): A 60 / 60 / 60, D 90 / 70 / 70,
E 90 / 70 / 80. Stuck-loop rate is 0 for every condition on every suite.

SimpleQA breakdown (%): abstained 0 / 0 / 0, empty answer 37 / 33 / 27, answered and
wrong 60 / 63 / 70 for A / D / E. Nobody abstains in v1; the 3% is one item.

E's 9B rung fired on 5 gsm8k items (5 right after it), 3 tools (3), 11 simpleqa (0),
4 ocrbench (1), 2 multistep (2), 0 humaneval.

What the traces say about the numbers:

- humaneval, D 63 vs A 90: of D's 11 misses, 5 are the verifier passing code it never
  tested and 4 are weak tests written while looking at the code. Nothing escalated
  because nothing was judged wrong, so E is identical to D there. This is the v2 fix
  list (PREREG-v2.md items 2-4).
- tools, D 80: two items ran the check on a bare number the verifier took for code, one
  had the right number in a tool output and the wrong one in the answer.
- simpleqa: the blind re-solve on closed-book trivia is two small models guessing at
  each other. The 9B does not help either (11 escalations, 0 right). A's 11 empty
  answers are all thinking past the token cap on the second retry, 9k tokens each. D's
  10 split: 5 the same, 4 where the verifier wrote a "test" for a trivia question and
  PASSed an empty answer as code (PREREG-v2 item 5 is the fix), 1 other.
- multistep: D and E beat A by 10-30 points every seed. A's misses are not wrong
  numbers: at seed 0 all four are empty answers (the 9B thinks past the cap after the
  tool ran), and across seeds the two-value items (fib digits, sqrt digits) come back
  with the digit count alone. One seed-2 answer is a raw plan envelope, truncated.
- ocrbench 45: all misses are misreads, mostly on small crops, not reasoning.
- D is the cheapest per second on every suite despite two swaps per item (4-5 s of
  loading). A's time goes into thinking; on simpleqa it thinks for three minutes.

### 5090, v1 vs v2

(filled in after the pod run; PREREG-v2.md has the conditions and hypotheses V1-V5)

## Which hypotheses held

On the 4070 v1 run, seed 0. B and C pending where noted.

- H1 (A >= D on gsm8k and humaneval): holds. 90 = 90 and 90 > 63.
- H2 (reliability): the stuck-loop half is vacuous, every condition is at 0, so the
  metric separated nothing. Variance on multistep is not lower for D or E (std 9 vs
  A's 0); what they have is 17-20 more points of mean accuracy. The simpleqa half
  needs B's unsupported rate (pending); against A it fails, D's 63 is not 10 below A's
  60. As pre-registered, H2 does not hold. The multistep gain is real and was not the
  claim.
- H3 (E closes >= 50% of the A - D gap at <= 60% of A's tokens): fails. The gsm8k gap is
  0. The humaneval gap is 27 points and E closed none of it, because the verifier never
  flagged the wrong code, so the rung never fired. Escalation only pays where the
  verifier catches the failure; the rung itself is fine (5/5 on gsm8k, 3/3 on tools).
- H4 (equal-compute voting): untested, B3 was cut.

## Things I noticed while judging

- The multistep judge is "every expected value appears in the answer". One A item answered
  with a truncated JSON envelope (the 9B hit the token cap mid-output) and still passed
  because "210" occurred in the text. The judge is fixed by PREREG; I list such cases here
  rather than re-judge them.
- SimpleQA correctness is string containment, a lower bound. The number that matters there
  is the unsupported rate (answered and wrong).
- OCRBench gold for some items is non-Latin text; the 4B produced mojibake on one, counted
  wrong.
