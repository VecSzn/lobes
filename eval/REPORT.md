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
   call index, commit 6c2a145) went into v2 before its 5090 runs; the 44 v2 items already
   run without it were discarded and rerun.
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
    classifies alone, 0f218c1) and the slot went to granite-4.0-h-1b, which gets 330 (4a98642).
    The labelled prompts are not eval items. The v3 D medium run and 89 items of D high had
    already finished on the earlier code (d947c71); they are kept as `5090-v3-d947c71` and
    `5090-v3-high-d947c71` (277/370 at medium, 13 of the 17 tools misses were tool tasks filed
    as code) and both conditions ran again on main.
11. Every v3 line ran with `--workers 4`, four items in flight on one box. Seconds are wall time
    under that contention for R and D alike, so they compare with each other and not with the
    v2 tables above, which ran one item at a time. Tokens and correctness do not depend on it.
    The pods also carry `threads: 8` and a 65536 context for the reasoning model (`eval/pod.sh`).
12. A line not in the PREREG table: the second version at high on the enlarged suites with the
    same intake fix, branch `v2-fix` (1d778d1 = fc7ff7e plus the model-only classifier, the
    granite executive and the language-lobe fixes), tag `5090-v2fix-high`, so that the v2 high
    milestone has a new-ruler number. `5090-pre-v3` stays the old classifier as pre-registered.
13. The v3 section below was written while D medium was at 275 of 370 items and D high at 39.
    It covers the finished suites only and is replaced when the runs end.

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

### 5090, v3 against the 9B alone on the enlarged suites, seed 0 (running)

Same items, same boxes as deviation 9, four workers each (deviation 11). R is the 9B as
shipped, one call per item (deviation 6). D is v3 at medium on `main` after the executive
change (deviation 10). Finished suites only; simpleqa, ocrbench and multistep follow.

| suite | n | R correct | D correct | R tokens | D tokens | R s | D s | R forced | D forced |
|-----------|-----|-----|-----|-------|------|------|------|----|----|
| gsm8k     | 200 | 184 | 181 | 10924 | 6805 | 51.1 | 30.5 | 73 | 41 |
| humaneval | 30  | 29  | 28  | 15786 | 6893 | 76.1 | 32.4 | 15 | 7  |
| tools     | 30  | 25  | 27  | 15176 | 8572 | 72.0 | 36.4 | 15 | 12 |
| the three | 260 | 238 | 236 | 11976 | 7019 | 56.4 | 31.4 | | |

Tokens are prompt plus completion over every call of the item, means; seconds are means of
wall time. Medians: gsm8k R 5562 tokens and 42 s, D 5302 and 27 s; humaneval R 17642 and 91 s,
D 3646 and 21 s; tools R 18085 and 93 s, D 5969 and 33 s. "Forced" counts items where a
thinking call ran out of budget and was made to answer (deviation 7): for R the 12000 cap,
for D the medium 6000. Longest D item: gsm8k 69 s, humaneval 137 s, tools 86 s; 5 of the 260
took over 60 s. D hit a per-item cap twice (tokens, both humaneval).

Over the three suites D is two items behind R on 59% of its tokens and 56% of its seconds.
Item by item on gsm8k: D misses 19, R 16, and 12 are the same items (962 among them, wrong
in every condition since v1). D alone misses 7, R alone 4. On humaneval both miss
145; D also misses 65. On tools nothing overlaps: R misses 21, 22, 23, 33, 49; D misses 29, 39
and 44, all three tool tasks the executive filed as code, which the code path then runs as an
implementation with no witness. That is the same failure as the 13 at d947c71, down from 11 of
the old 30 to 3.

How the witnesses behaved on gsm8k, the suite with enough items to say: the first two
(motor, reasoning) agreed on 121 of 200 and were right on 116 of those; a third witness was
drawn 79 times and settled 8 more; 73 items ended with no majority and went out as the
reasoning lobe's value with "not sure" in front, and 59 of those 73 were right. Of the 127
items settled on evidence, 122 are right (96.1%); the 5 wrong ones are 962, 823, 1016, 711
and 1195, four of them wrong for the 9B too. The disagreement comes mostly from the motor
lobe: its program printed nothing on 33 of 200 items, 31 of them among the 73 with no
majority. The reasoning lobe was forced out of its 6000-token thinking budget on 41 items,
22 of them among the 79 that needed a third witness.

Early reads of PREREG-v3, to be written up properly when the runs end: W2 holds so far
(181 against 184, one point down, within the 2 allowed; 1252 and 413 are among the 200 and
right, both hedged). W3 holds on gsm8k and tools; W4 fails on the longest item (137 s at medium, under
four workers) and holds on caps (2 of 260 under the 2%); W5 fails on the rate (first two
disagree on 39.5%, not under 25%) and misses on the precision by one item (96.1% against
97%), and the mechanism reading is above: the agreed-and-wrong items are the gold-disputed
ones, the disagreement is the motor lobe returning nothing; W8 holds (28 against 26).

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

## Things I noticed while judging

- The multistep judge is "every expected value appears in the answer". One A item answered
  with a truncated JSON envelope (the 9B hit the token cap mid-output) and still passed
  because "210" occurred in the text. The judge is fixed by PREREG; I list such cases here
  rather than re-judge them.
- SimpleQA correctness is string containment, a lower bound. The number that matters there
  is the unsupported rate (answered and wrong).
- OCRBench gold for some items is non-Latin text; the 4B produced mojibake on one, counted
  wrong.
