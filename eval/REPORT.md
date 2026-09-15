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
   call index) went into v2 before its 5090 runs; the 44 v2 items already run without it
   were discarded and rerun.
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
   it and R runs with it; the tables count how often it fired.
8. The v2 A/D/E run died after 248 items, on D s0 simpleqa-1404: the answer carried a U+2028
   line separator, Python's splitlines() breaks on that, and reading the trace back failed.
   Fixed by splitting on newlines only; D/E resumed from where they stopped, after B/C, which
   had started in the meantime.
9. The rest of the queue was split over two 5090 boxes to finish sooner: the first keeps v2 D/E
   and v2 high, a second one from the same template (driver 580 instead of 570, same llama.cpp
   build, same model files and item ids) runs R. Before the
   split, R shared the first GPU with the D/E lane for nine minutes: the 22 D s0 items finished
   22:02-22:11 UTC (12 ocrbench, the 10 multistep) have inflated ms, nothing else about them
   changes; the 8 R items from that stretch were thrown away and R starts over on the second
   box. A double launch in the same minutes ran the D/E eval twice for five minutes and recorded
   ocrbench-610 twice; the second record was dropped.
10. v3 (PREREG-v3.md): the executive changed after the pre-registration. The 1.2B classified
    157 of 341 labelled prompts at best, so the rules it needed were removed (the model
    classifies alone) and the slot went to granite-4.0-h-1b, which gets 330. The labelled
    prompts are not eval items. The v3 D medium run and 89 items of D high had already
    finished on the earlier code; they are kept as `5090-v3-old-intake` and
    `5090-v3-high-old-intake` (277/370 at medium, 13 of the 17 tools misses were tool tasks
    filed as code) and both conditions ran again on main.
11. Every v3 line ran with `--workers 4`, four items in flight on one box. Seconds are wall time
    under that contention for R and D alike, so they compare with each other and not with the
    v2 tables above, which ran one item at a time. Tokens and correctness do not depend on it.
    The pods also carry `threads: 8` and a 65536 context for the reasoning model (`eval/pod.sh`).
12. A line not in the PREREG table: the second version at high on the enlarged suites with the
    same intake fix, branch `v2-fix` (that runtime plus the model-only classifier, the granite
    executive and the language-lobe fixes), tag `5090-v2fix-high`, so that the v2 high
    milestone has a new-ruler number. `5090-pre-v3` stays the old classifier as pre-registered.
13. The v3 D medium run that finished on 09-14 (`5090-v3-last-number`, all 370 items: gsm8k 181,
    humaneval 28, tools 27, simpleqa 2, ocrbench 30, multistep 19) compared values on their
    last number only. Its multistep traces (19 against the 9B's 25) showed two contract holes
    rather than item noise: five "write the file, then report" tasks were filed as code by the
    executive and the item delivered source; and a motor program that printed one value fewer
    than asked still "agreed" with the reasoning lobe on the last number. The contract changed:
    a value is one line per asked quantity, in order; agreement is line by line; the code path
    is entered only when the reasoning lobe hands over source, the executive's label no longer
    routes there. Replaying the 370 recorded answers under the new judge moved four verdicts,
    all misses. Found on the way: granite writes newlines as a literal `\n` inside JSON, and a
    one-line program starting with a `#` comment compiles with everything after the comment
    swallowed; 23 of the 33 empty motor outputs on gsm8k were that. D medium and D high ran
    again on the new contract; R and the second version did not need to.
14. Between that and the shipped code the v3 tree changed five more times on 09-15, each after
    reading the first 60 items (multistep, tools) of a run. The partial runs are kept:
    `5090-v3-values` (one value per line, reasoning thinking first: multistep 24, tools 12 of
    12 then stopped, 13.1k tokens and 53 s an item), `5090-v3-cheap-first` (the reasoning lobe
    thinks only when three plain witnesses disagree: multistep 23, tools 28, 8.0k / 2.7k
    tokens; two judge bugs found here, `same()` emptied "a" to nothing and `restates()` took
    the goal's own numbers for a program's output), `5090-v3-cheap-first-2` (those fixed:
    multistep 25, tools 27, gsm8k 89/101; tools-34 lost to two plain witnesses agreeing on a
    value a program had refuted), `5090-v3-settle` (a program that ran cannot be outvoted by
    written values, and no JSON means no value: multistep 23, tools 29, gsm8k 169/198 against
    179 for the last-number run and 183 for the 9B on the same items, 4.6k tokens an item;
    nine of the ten lost gsm8k items are the reasoning and motor lobes, both plain, misreading
    the same clause and agreeing on the same wrong number), `5090-v3-settle-words` (`same()`
    on whole words after "e" matched "wrote 71 chars"; 113 items, stopped when the next change
    landed). The choice put to the user was tokens or accuracy; accuracy won. The shipped code
    has the reasoning lobe thinking from its first sample again, `n` thinking samples in all,
    with the settle rule, the line contract and the whole-word match kept. PREREG-v3's "two
    that agree settle it" so carries one more clause: once any program ran, the agreeing pair
    must include one that ran. The account, run by run, is in docs/DECISIONS.md.
15. The per-item token caps (16000 at medium, 40000 at high) were sized while the reasoning
    lobe thought only on disagreement. With thinking first, a sample that runs out of its
    thinking budget costs about 12.7k tokens (its reasoning goes back in as prompt for the
    forced answer), so medium holds one such sample and the cap ends an item at its fourth or
    fifth witness. Cap hits are counted in the tables below and were not rerun.

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

### 5090, v1 vs v2, seed 0, effort medium

Same box, same item ids, same seed; v1 is the tag, v2 the branch. B, C and R exist only at
v2. Accuracy %:

| suite     | A v1 | A v2 | D v1 | D v2 | E v1 | E v2 | B v2 | C v2 | R    |
|-----------|------|------|------|------|------|------|------|------|------|
| gsm8k     | 93.3 | 93.3 | 93.3 | 90.0 | 90.0 | 90.0 | 80.0 | 90.0 | 96.7 |
| humaneval | 73.3 | 83.3 | 70.0 | 80.0 | 66.7 | 80.0 | 83.3 | 83.3 | 90.0 |
| tools     | 95.0 | 95.0 | 90.0 | 90.0 | 75.0 | 95.0 | 70.0 | 90.0 | 90.0 |
| simpleqa  | 6.7  | 3.3  | 6.7  | 10.0 | 3.3  | 6.7  | 3.3  | 10.0 | 6.7  |
| ocrbench  | –    | –    | 45.0 | 60.0 | 45.0 | 65.0 | 55.0 | 55.0 | –    |
| multistep | 60.0 | 70.0 | 70.0 | 90.0 | 90.0 | 90.0 | 90.0 | 80.0 | 100  |

Over the 120 items every condition ran (everything but ocrbench), v2: R 71.7, C, D and E
67.5, A 66.7, B 60.8.

Cost at v2, means per item. Seconds are wall time on the 5090, tokens are prompt plus
completion summed over every call of the item:

| suite     | R s  | A s  | B s  | C s  | D s  | E s  | R tok | A tok | B tok | C tok | D tok | E tok |
|-----------|------|------|------|------|------|------|-------|-------|-------|-------|-------|-------|
| gsm8k     | 27.3 | 10.0 | 5.6  | 7.3  | 10.2 | 11.6 | 10418 | 3244  | 2043  | 2678  | 3633  | 4110  |
| humaneval | 44.8 | 10.9 | 11.6 | 13.8 | 10.1 | 14.7 | 17637 | 3299  | 4320  | 5001  | 3812  | 5191  |
| tools     | 18.7 | 7.5  | 4.4  | 5.4  | 6.6  | 6.4  | 5945  | 2272  | 1745  | 2106  | 2236  | 2266  |
| simpleqa  | 53.4 | 20.0 | 10.3 | 10.5 | 15.8 | 26.9 | 22539 | 5930  | 3834  | 6323  | 7934  | 11424 |
| ocrbench  | –    | –    | 14.1 | 15.2 | 19.5 | 18.9 | –     | –     | 6248  | 6922  | 6536  | 8566  |
| multistep | 38.2 | 12.2 | 10.9 | 6.2  | 26.1 | 22.7 | 14460 | 3756  | 3908  | 2395  | 7062  | 7680  |

Over the 120 shared items: R 37.7 s and 8200 completion tokens per item (14844 with prompts;
a forced answer sends the reasoning back in as prompt), A 12.5 s and 2131, B 8.5 and 1642,
C 9.3 and 1828, D 12.3 and 2538, E 16.3 and 3443. D's ocrbench and multistep seconds include
the nine contended minutes of deviation 9. Swaps are 0 on this box, everything stays loaded
under the 28 GB budget, so the 4070 tables above are the only swap numbers there are.

SimpleQA, %: abstained R 0 / A 43.3 / B 73.3 / C 23.3 / D 26.7 / E 23.3; unsupported
(answered, not abstained, wrong) R 93.3 / A 46.7 / B 20.0 / C 63.3 / D 56.7 / E 70.0; empty
R 0 / A 6.7 / B 3.3 / C 3.3 / D 6.7 / E 0. v1 unsupported on this box: A 63.3, D 70.0, E 83.3.

multistep across seeds, s0 / s1 / s2 and mean: R 100 / 100 / 90 (96.7), A 70 / 50 / 60 (60.0),
B 90 / 70 / 80 (80.0), C 80 / 80 / 90 (83.3), D 90 / 90 / 70 (83.3), E 90 / 90 / 80 (86.7).
v1 on this box: A 66.7, D 76.7, E 83.3.

The seed fix held: of the 243 votes in v2 A-E (A 49, B 61, C 43, D 36, E 54), none drew three
identical samples. In v1 all of them did.

Forced answers exist only in R here (v2 A-E ran before the change): gsm8k 11 of 30, humaneval
19 of 30, tools 3 of 20, simpleqa 27 of 30, multistep 13 of 30, 73 of 140 items. Eight R items
still ended on the length limit after the forced answer, 7 simpleqa and 1 multistep.

The 9B on its own leads on gsm8k, humaneval and multistep and ties A on tools and simpleqa,
at three times the wall time of any scaffolded condition: thinking runs at the template
default and hits the 12000 cap on half the items, while A at medium thinks only on the retry
with a 6000 cap. R against A is a thinking-budget comparison as much as a scaffolding one;
the D high and auto runs in the next section are the closer match. R has no images and
nothing to abstain with: it answers all 30 simpleqa items and is wrong on 28.

E's rung fired on 21 items: gsm8k 3, humaneval 3, simpleqa 7, ocrbench 5, multistep 3, tools
0. Item by item against D: on the 8 the 9B got right, D's own answer had been right as well
(the verifier had refused to pass it); on 2, simpleqa-1390 and multi-08, D was right and the
9B's answer was wrong; on the other 11 both were wrong, 7 of them simpleqa. The three items
where E beats D (tools-01, ocrbench-660, multi-03) never escalated, that run just sampled
differently. So at v2 the rung turned 0 answers from wrong to right and 2 from right to
wrong. The cause is upstream: of D's 11 misses on gsm8k, humaneval and tools, the verifier
passed 10, and it flagged 8 answers that were right. It sends the 9B the wrong items.

What the traces say:

- gsm8k: 962 is missed by all six conditions with the same answer, $600. The question gives
  supplies ($400) and tickets (50% more) and asks for the cost of travel; the gold adds the
  two, every model gives the tickets. 1252 (C, D, E answer 40 for 70, and the blind re-solve
  agrees) and 413 (D, E:
  125 for 25, computed in python from the wrong setup) are misreads of chained ratios. 349
  has a gold of 2640 that takes four weeks to the month; A answered 1540, B 2740.
- humaneval: 145 (order_by_points, the sign of a negative number's first digit) falls for
  everyone, R included; 125 (split_words) for every scaffolded condition and not for R. D's
  six: 84, 142 and 125 passed a blind-written test that the hidden tests then failed; 83 and
  163 have no examples in the docstring and the verifier wrote no test, PASS on nothing; 145
  was flagged, retried twice, still wrong. A's five: 125 no test, 77 a test that crashed on
  its own name, 83 and 163 a failing test on the first pass and a crashing one on the retry,
  145 retried out. The v1 hole, untested code passing, is narrower but not closed: "no test"
  and "the test broke" both end in PASS with basis none.
- tools: D's tools-01 (sha256) is the worst trace of the run. python printed the right hash,
  20515122, twice. The reasoning lobe (4B) answered "unknown" because the output "did not
  match the expected", the verifier's blind re-solve (gemma) made up 232f8241 and asked for a
  tool check, the check printed 20515122 again, the reasoning lobe then claimed 232f8241...
  as the hash, and the verifier passed it on consistency, "blind re-solve agrees". Two models
  agreed on a guess one of them had made while the right number sat in tool_0.json and
  tool_1.json. The v2 rule that a tool's stdout wins a conflict never ran because there was
  no conflict. tools-04 (D, E: "There are 168 prime numbers below 1000") is the strict number
  judge reading the last number in the answer; the lenient row counts it. A's tools-09: the
  9B filed "reverse this string" as math with no tool, reversed it by hand and dropped a
  letter, and its own blind re-solve made the same slip, so consistency with itself passed
  it. In the single-model conditions the blind re-solve is the same model asked again.
- simpleqa: the hedge moved wrong answers into abstentions: A 63 to 47 unsupported, D 70 to
  57, B down to 20 by abstaining on 73%. C and E hedge less and sit at 63 and 70. Nothing
  here knows the answers; the only lever is how often the system says so.
- ocrbench: four of the twenty items (904, 950, 961, 991) are formulas whose gold is LaTeX
  split into single tokens, and the containment judge wants that string; no condition matched
  any of them, so 80 is the ceiling under this judge. Of the other 16 D reads 12; the misses
  are 222 (9557 read as 955-7), 660 (the wrong bar on a chart), 160 ("Drugs" read as "Ding"),
  304 (a book cover, author instead of title). B and C miss the same eight plus 610; there
  the second look comes from the same 4B-vl that made the first read.
- multistep: A's three misses are one-value answers to two-value questions: "6" for fib(30)
  and its digit count (hedged), "5" for the 15th digit without the 20-place value, "310.2 K"
  without the celsius. The verifier's blind answer on multi-09 had both values; the language
  pass kept the last one. D's one miss has the day count right and the weekday wrong, Friday
  for Monday, two models agreeing. R writes both values every time.

On the same v1 code, seed and items, the 4070 and the 5090 differ by 17 points on A humaneval
(90 / 73.3), 20 on D multistep (90 / 70), 10 on D tools (80 / 90) and 7 on D humaneval (63 /
70). Nothing in the code knows which GPU it is on; llama.cpp rounds differently on each and a
30-item suite moves 3.3 points per item. Read any difference under about 15 points in this
report as inside that band unless it points the same way in every condition.

### 5090, v3 against the 9B alone on the enlarged suites, seed 0

Same items, same boxes as deviation 9, four workers each (deviation 11). R is the 9B as
shipped, one call per item (deviation 6). D medium and D high are v3 as it ships, the
contract of deviations 13 to 15, results `5090-v3` and `5090-v3-high`; the fourth line is
branch `v2-fix` at high (deviation 12), results `5090-v2fix-high`. The 9B takes no images,
so it has no ocrbench.

| suite | n | R | D medium | D high | v2-fix high |
|-----------|-----|-----|-----|-----|-----|
| gsm8k     | 200 | 184 | 177 | 182 | 167 |
| humaneval | 30  | 29  | 27  | 28  | 27  |
| tools     | 30  | 25  | 30  | 30  | 29  |
| multistep | 30  | 25  | 25  | 25  | 17  |
| simpleqa  | 30  | 5   | 2   | 2   | 1   |
| ocrbench  | 50  |     | 30  | 30  | 32  |
| the 320 R runs | 320 | 268 | 261 | 267 | 241 |
| all 370   | 370 |     | 291 | 297 | 273 |

Tokens are prompt plus completion over every call of the item, means; seconds are means of
wall time under four workers.

| suite | R tokens / s | D medium | D high | v2-fix high |
|-----------|--------------|--------------|--------------|--------------|
| gsm8k     | 10924 / 51.1 | 6557 / 27.5  | 8176 / 37.0  | 15604 / 54.5 |
| humaneval | 15786 / 76.1 | 8382 / 36.8  | 16114 / 74.5 | 17051 / 72.3 |
| tools     | 15176 / 72.0 | 7809 / 33.5  | 17311 / 74.1 | 11781 / 36.5 |
| multistep | 19182 / 88.0 | 16659 / 70.3 | 26408 / 120.7 | 20197 / 72.9 |
| simpleqa  | 20349 / 86.1 | 19237 / 78.6 | 38989 / 177.2 | 33547 / 106.2 |
| ocrbench  |              | 8925 / 40.9  | 12909 / 49.3 | 36095 / 125.4 |
| the 320   | 13436 / 62.2 | 8981 / 37.7  | 14375 / 65.0 | 17494 / 61.1 |

Medians are well under the means everywhere: gsm8k R 5562 tokens and 42 s, D medium 3882
and 17 s, D high 3394 and 13 s; humaneval R 17642 and 91 s, medium 4947 and 30 s, high 3864
and 25 s; tools R 18085 and 93 s, medium 5797 and 35 s, high 6864 and 40 s. The means are
carried by the items that disagree and draw witnesses. "Forced" counts items where a
thinking call ran out of its budget and was made to answer (deviation 7): R 147 of 320
(the 12000 cap), D medium 128 of 370 (6000), D high 92 (16000), v2-fix 49. "Capped" counts
items that hit the per-item token cap between witnesses (deviation 15): medium 27 of 370
(gsm8k 5, humaneval 4, multistep 7, simpleqa 9, ocrbench 2; 5 of the 27 right), high 21
(gsm8k 5, humaneval 2, multistep 3, simpleqa 11; 3 right), v2-fix none. The cap is checked
between witnesses, so a capped item ends past it: medium 16149 to 28296 tokens against
16000, high 40142 to 89787 against 40000. Longest items: R 119 s, 167 of 320 over 60 s;
medium gsm8k-186 at 129 s, 79 of 370 over 60 s; high HumanEval-145 at 450 s, 116 over 60 s.

On the 320 items the 9B runs, D medium is seven behind on 67% of its tokens and 61% of its
seconds; D high is one behind on 107% of its tokens and 104% of its seconds. High costs
more than medium on every suite (gsm8k 1.25x the tokens, humaneval 1.9x, tools 2.2x,
multistep 1.6x, simpleqa 2.0x): the thinking budget is 16000 instead of 6000, and a forced
sample is about 32.8k tokens instead of 12.7k because the thinking is sent back as prompt.
Simpleqa at high is the worst case, 177 s an item, because a closed-book item needs three
samples in a row to agree and five are drawn before it gives up.

Item by item. gsm8k: medium misses 23, R 16, 10 shared; medium alone misses 13, R alone 6
(1071, 1166, 1185, 641, 711, 93). High misses 18, 11 shared with R; high alone 7 (1056,
1070, 298, 353, 380, 611, 943), R alone 5 (1071, 1166, 1185, 652, 781). 962 is wrong in
every condition since v1. High fixes 10 of medium's misses and breaks 5. humaneval: all
three miss 145; medium also 125 and 163, high also 125. tools: R misses 21, 22, 23, 33 and
49, v3 none at either level, and the three tool tasks the executive filed as code in the
last-number run (29, 39, 44, deviation 13) are right. multistep: R misses 13, 16, 18, 27 and
14; medium misses 14, 11, 20, 31, 37; high misses 14, 15, 31, 35, 37. simpleqa: R answers
all 30 and is wrong on 25; medium abstains on 22, answers wrong on 5, gives an empty answer
on 2, is right on 2 (one of them hedged); high abstains on 26, is wrong on 3, right on 2.
ocrbench: 30 of 50 at both levels, the same 20 misses; v2-fix 32, `pre-v3` 33.

What the high misses look like, from the traces. multi-15: both programs take the same year
off by one and agree. multi-35: two forced thinking samples of 32.8k tokens each end in
placeholder values. gsm8k-298: the reasoning lobe's program prints 400, motor's 350, the
verifier prints the running totals 200 to 400 and its tail matches 400, gold 500. gsm8k-1056
and 711: the reasoning lobe writes a program whose `def check()` is never called, so nothing
is printed and its written value stands in; on 711 that value and the verifier agree on 4
(gold 6), on 1056 the first sample's 156 is right but finds no partner and two hot samples
agree on 104. None of these is a contract hole; they are two witnesses reading the same
words the same way.

How the witnesses behaved on gsm8k, the suite with enough items to say. Medium: the first
two (reasoning thinking, motor plain) agreed on 132 of 200 and were right on 123 of those
(93.2%); a third witness or more was drawn 68 times and those ended right 54 times; 10 items
ended with no majority and went out as the reasoning lobe's value with "not sure" in front,
5 of them right. Of the 190 items settled on evidence, 172 are right (90.5%). Witness count
per item: two on 132, three on 8, four on 51, five on 9; the reasoning lobe was called 269
times, motor 200, the verifier 68. Motor's program printed nothing on 9 items (33 in the
last-number run before the granite `\n` fix, deviation 13). Forced out of the 6000-token
thinking budget on 39 items, 19 of them among the 68 that drew a third witness. By pair:
motor's program and the reasoning lobe's program settled 107 (100 right; the 7 wrong are
962, 823, 1016, 494, 652, 12, all wrong for the 9B too, and 752); motor's program and a
written reasoning value 30 (28 right; 943, 1247 wrong); two reasoning programs 21 (19);
a written value and a reasoning program 20 (18); a program and the verifier 8 (4 right);
nothing agreed on 11 (5 right). Re-settling the recorded witnesses with only the first
three (no hot samples) gives 170 instead of 177: the hot samples fixed 7 and broke none.

High: the first two agreed on 125 (120 right, 96.0%); a third or more was drawn 75 times
(62 right); no majority 8 (5 right). Evidence 190 items, 176 right (92.6%); two settled on
consistency, one right. Two witnesses on 125, three 9, four 54, five 7, six 2, seven 3
(five thinking samples plus motor and the verifier). Motor printed nothing on 14, forced 22. Pairs: two programs 98 (91 right),
motor's program and a written value 32 (31), two reasoning programs 24 (24), a written
value and a reasoning program 25 (20), a program and the verifier 8 (7), a written value
and the verifier 1 (0, that is 711), nothing 9 (6). First three only: 179 instead of 182,
the hot samples fixed 4 and broke 1.

Tools and multistep at medium: on tools the first two agreed on 27 of 30 and every one was
right, the other 3 settled on a later witness, also right. On multistep the first two agreed
on 11 (10 right), 17 drew more (14 right), 7 ended with no majority (3 right); the thinking
budget was hit on 23 of 30 items, which is why multistep costs 16659 tokens at medium.

## Which hypotheses held

On the 4070 v1 run, seed 0 (PREREG.md H1-H4). B and C were stopped part way on the 4070 and
run on the 5090 at v2 instead, so their v1 columns stay empty.

- H1 (A >= D on gsm8k and humaneval): holds. 90 = 90 and 90 > 63.
- H2 (reliability): the stuck-loop half is vacuous, every condition is at 0, so the
  metric separated nothing. Variance on multistep is not lower for D or E (std 9 vs
  A's 0); what they have is 17-20 more points of mean accuracy. The simpleqa half
  fails against A, D's 63 is not 10 below A's 60, and B never finished on the 4070; at
  v2 on the 5090 B's rate is 20 against D's 57, the wrong way round. As pre-registered,
  H2 does not hold. The multistep gain is real and was not the claim.
- H3 (E closes >= 50% of the A - D gap at <= 60% of A's tokens): fails. The gsm8k gap is
  0. The humaneval gap is 27 points and E closed none of it, because the verifier never
  flagged the wrong code, so the rung never fired. Escalation only pays where the
  verifier catches the failure; the rung itself is fine (5/5 on gsm8k, 3/3 on tools).
- H4 (equal-compute voting): untested, B3 was cut.

On the 5090, v1 vs v2 (PREREG-v2.md V1-V5), seed 0.

- V1 (each change lifts its suite, nothing else drops more than 5): holds for humaneval
  (D 70 to 80), ocrbench (45 to 60) and the simpleqa wrong rate (70 to 57); tools did not
  move, 90 to 90, the two misses left are the number judge and the hash trace above.
  Largest drop is gsm8k, 3.3. Nothing is reverted.
- V2 (D reaches A on tools and multistep, within 5 on gsm8k and humaneval): multistep 90
  vs 70, gsm8k 90 vs 93.3, humaneval 80 vs 83.3, tools 90 vs 95. One item short on tools,
  otherwise holds.
- V3 (D ocrbench >= 65): fails, 60; E gets 65. Under this judge the four formula items
  are out of reach for everyone, so D reads 12 of the 16 it could.
- V4 (D unsupported <= 20%, confident correct within 3 of v1): fails on the first half,
  56.7; the second holds, 10 against 6.7. Only B gets under 20, by abstaining on 73%.
- V5 (v1 on the 5090 within 5 points of v1 on the 4070): fails, by up to 20 points on
  30-item suites with the same code and seed. As pre-registered, the v1 vs v2 comparison
  above uses the 5090 numbers only, and that band is the error bar for every table here.

On the 5090, v3 against the 9B alone (PREREG-v3.md W1-W8), seed 0, v3 at medium unless
said. Six of the eight fail.

- W1 (multistep and tools each at least 10 points over R, and v3 the right one more often
  when exactly one is right): tools holds, 30 against 25 is 16.7 points, and the five
  items where exactly one is right are all v3's. multistep fails, 25 against 25; the eight
  items where exactly one is right split four and four.
- W2 (gsm8k not below R minus 2 points; 1252 and 413 right): fails at medium, 177 against
  184 is 3.5 points down. 1252 and 413 are right at both levels. High, 182 against 184,
  would be inside the 2 points, but the hypothesis named medium.
- W3 (tokens and seconds below R on gsm8k, multistep, tools and simpleqa; below
  `5090-v2-high` on every suite): the first half holds on all four suites, tokens and
  seconds both. The second half fails: on the items the two runs share (the first 30 of
  gsm8k, humaneval, simpleqa, the first 20 of ocrbench; the tools and multistep items were
  replaced), gsm8k is 6897 against 5024 and simpleqa 19237 against 15286; humaneval 8382
  against 8697 and ocrbench 9239 against 16773 are below.
- W4 (no item exceeds the medium caps, at most 2% hit one, longest under 60 s): fails on
  all three. 27 of 370 hit the token cap (7.3%), and because the cap is checked between
  witnesses every one of them ended past it, up to 28296 against 16000. The longest item
  is 129 s and 79 items are over 60 s, under four workers.
- W5 (first two witnesses agreed are right 97% of the time, disagreement under 25%):
  fails on both. The first two agreed on 132 of 200 (disagreement 34%) and were right on
  123 (93.2%). The mechanism, as the prereg asked: of the 9 agreed-and-wrong items, 6 are
  wrong for the 9B too (962, 823, 1016, 494, 652, 12), so those are the question read
  the same way by every model, gold-disputed or a standard misreading, not a shared bug of
  the two programs; the other 3 (752, 943, 1247) are motor's program agreeing with the
  reasoning lobe on a wrong value, twice on the tail of a longer printout. The witnesses are independent on arithmetic and on code
  slips and not on how they read the words, because they read the same words. At high the
  first two agree on 125 (disagreement 37.5%) and are right on 120 (96.0%), still short.
- W6 (ocrbench at or above `pre-v3` D, and 5 points above the single 4B on the first 20):
  fails, 30 against 33, and 10 against 11 on the first 20.
- W7 (simpleqa unsupported not above `pre-v3` D, confident correct not below it by more
  than one): holds, unsupported 5 against 18, confident correct 1 against 2.
- W8 (humaneval within 2 items of v2 D high, 26/30): holds, 27, and 28 at high.

## Things I noticed while judging

- The multistep judge is "every expected value appears in the answer". One A item answered
  with a truncated JSON envelope (the 9B hit the token cap mid-output) and still passed
  because "210" occurred in the text. The judge is fixed by PREREG; I list such cases here
  rather than re-judge them.
- SimpleQA correctness is string containment, a lower bound. The number that matters there
  is the unsupported rate (answered and wrong).
- OCRBench gold for some items is non-Latin text; the 4B produced mojibake on one, counted
  wrong.
