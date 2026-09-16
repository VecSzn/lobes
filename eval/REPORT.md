# Eval report

What happened when the rules in eval/PREREG.md, PREREG-v2.md, PREREG-v3.md and PREREG-v4.md
were run. Everything below comes from `eval/results/*.jsonl` (the per-item records ship with the
release, not in git); `lobes eval --report` prints the tables. The current numbers come first, then
the pre-registered hypotheses, the deviations from the pre-registrations in the order they
happened, and the earlier versions.

## Results: the items PREREG-v4 added, 5090, seed 0

v3 ended on a table where four of the numbers it wanted to compare sat inside a six-item band,
and tools was 30/30 twice. v4 keeps every v3 item untouched and adds 90 harder ones: tools
`tools-50` to `tools-79`, multistep `multi-40` to `multi-69`, and AIME 2025. Only those 90 run
in each v4 condition. GSM8K, HumanEval, OCRBench, SimpleQA and the old halves of tools and
multistep carry their v3 records forward into the v4 files (deviation 22), so the section after
this one is still the current result for them.

| the items v4 added | n | 9B alone | medium | medium again | high |
|---|---|---|---|---|---|
| tools, 50-79           | 30 | 11 | 30 | 28 | 29 |
| multistep, 40-69       | 30 | 20 | 25 | 28 | 29 |
| those two together     | 60 | 31 | 55 | 56 | 58 |
| AIME 2025              | 30 | 13 | 12 | 15 | 16 |
| all 90                 | 90 | 44 | 67 | 71 | 74 |

Tokens are prompt plus completion over every call of the item, means; seconds are means of wall
time under four workers.

| the items v4 added | 9B tokens / s | medium | medium again | high |
|---|---|---|---|---|
| tools, 50-79     |  6662 / 54.4 | 12776 / 50.8 | 14287 / 57.1 | 27368 / 120.6 |
| multistep, 40-69 |  5260 / 41.1 | 16425 / 68.1 | 15670 / 64.6 | 35747 / 162.2 |
| those 60         |  5961 / 47.7 | 14601 / 59.5 | 14979 / 60.9 | 31558 / 141.4 |
| AIME 2025        |  6647 / 50.6 | 30768 / 122.6 | 30337 / 122.4 | 60726 / 286.2 |
| all 90           |  6189 / 48.7 | 19990 / 80.5 | 20098 / 81.4 | 41280 / 189.7 |

The band first, because nothing below means anything without it. The two medium runs are the
same code on the same box back to back, and on these 90 items they score 67 and 71: a band of
4, the same width as v3's. Ten items changed verdict, seven wrong to right and three the other
way, and only 56 of the 90 ended on the same answer string. Per suite the identical-answer rate
says where the noise lives: tools 28 of 30 identical and 2 flips, multistep 19 and 5, AIME 9 and
3. A gap of 4 or less on these tables is not a result.

Two gaps clear it by a wide margin, and they are the point of the round.

- **tools 50-79: 30 and 28 against the 9B's 11.** The old 30 were at their ceiling for this
  runtime (30/30 in every run since v2) and the 9B scored 25 there, a gap of 5 that a 30-item
  suite cannot resolve. On items whose value needs a computation long enough that no model of
  this size reaches it by writing, the gap is 17 to 19 points. The 9B fails them the way it was
  always going to: it writes a plausible number. This runtime runs a program and reads what it
  printed.
- **multistep 40-69: 25 and 28 against 20.** Smaller, 5 to 8 points against a band of 4, so it
  clears but not comfortably. The old half separates nothing at all now (25, 27 and 25 against
  25), so the whole multistep signal is in the new items.

What this gap is not: it is not six lobes against one brain. PREREG-v4 says this in advance and
it holds after the fact. Both suites reward running a program, and the 9B in condition R has no
tools, so part of the 17 points on tools is the architecture and part is having a python
interpreter. Nothing here separates the two.

The cost direction inverts on these items, and the v3 headline does not carry onto them. Over
the 60 tools and multistep items this runtime spends 2.45x the 9B's tokens and 1.25x its
seconds; on the 90 with AIME in, 3.2x and 1.65x. On the v3 suites it was 59% of the tokens and
55% of the seconds. Both statements are true of the items they were measured on, and neither
generalises: the v3 items are ones a single sample gets right, where the split saves the 9B's
long think, and the v4 items are ones that need several witnesses and a program each, where it
pays for them. tools is the interesting middle, 1.9x the tokens but 0.93x the seconds, because
the 9B spends its 6662 tokens thinking serially and this runtime spends its 12776 across calls
that overlap.

High buys 3 items over the top of the medium band (74 against 67 and 71) for 2.07x the tokens
and 2.36x the seconds. That is the same verdict as v3 and this was the round that was supposed
to overturn it: PREREG-v4 predicted AIME would be the first item set where the effort knob had
something to separate, and on AIME high scores 16 against medium's 12 and 15, which is inside
the three-sample spread of medium itself. The knob still does not pay for itself.

AIME is the boundary of what this design does. Three medium samples of it exist, counting the
pilot: 16, 12 and 15. The 9B alone scores 13, inside that spread, on a fifth of the tokens. Ten
items are right in both the 9B's run and the second medium run, 3 only the 9B, 5 only this
runtime, so it is not that the two are solving the same problems. What AIME rewards is one long
chain of reasoning held together, which is exactly what a larger model's own thinking does well
and what splitting a problem across witnesses that never see each other does not. Every one of
the 16 right answers in the pilot came from a witness program that ran, and so did 12 of the 14
wrong ones, so the mechanism fires; it fires on the wrong problems. This is a real limit and it
is recorded rather than dropped, because the suite was pre-registered before the numbers existed.

## Results: Lobes against the bare 9B, 5090, the enlarged suites, seed 0

Same items, same boxes as deviation 9, four workers each (deviation 11). R is the 9B as
shipped, one call per item (deviation 6). D medium and D high are the runtime as it ships,
the contract of deviations 13 to 18, results `5090-v3-witness` and `5090-v3-witness-high`.
The 9B takes no images, so it has no ocrbench. Every earlier run is in the appendix. The tools
and multistep rows here are the 30 items each that v3 ran, `tools-20` to `49` and `multi-10` to
`39`; the 30 v4 added to each are the section above and are not mixed into these numbers.

| suite | n | R | D medium | D medium again | D high |
|-----------|-----|-----|-----|-----|-----|
| gsm8k     | 200 | 184 | 182 | 181 | 181 |
| humaneval | 30  | 29  | 29  | 27  | 28  |
| tools     | 30  | 25  | 30  | 30  | 30  |
| multistep | 30  | 25  | 27  | 25  | 26  |
| simpleqa  | 30  | 5   | 4   | 3   | 2   |
| ocrbench  | 50  |     | 36  | 37  | 36  |
| the 320 R runs | 320 | 268 | 272 | 266 | 267 |
| all 370   | 370 |     | 308 | 303 | 303 |

"D medium again" is the same code on the same box a second time, `5090-v3-witness-2`; it
is there so every gap in this table can be read against the size of the noise.

Tokens are prompt plus completion over every call of the item, means; seconds are means of
wall time under four workers.

| suite | R tokens / s | D medium | D medium again | D high |
|-----------|--------------|--------------|--------------|--------------|
| gsm8k     | 10924 / 51.1 | 5308 / 23.8  | 5990 / 26.5  | 7869 / 37.7  |
| humaneval | 15786 / 76.1 | 6516 / 27.6  | 4200 / 20.2  | 8532 / 46.2  |
| tools     | 15176 / 72.0 | 8134 / 35.2  | 8102 / 36.6  | 19867 / 94.5 |
| multistep | 19182 / 88.0 | 14248 / 60.8 | 14354 / 61.5 | 24791 / 123.3 |
| simpleqa  | 20349 / 86.1 | 19841 / 80.9 | 16748 / 71.5 | 45388 / 226.1 |
| ocrbench  |              | 11109 / 53.8 | 11115 / 54.1 | 15857 / 69.6 |
| the 320   | 13436 / 62.2 | 7887 / 34.0  | 7813 / 34.3  | 14160 / 69.5 |
| all 370   |              | 8322 / 36.7  | 8259 / 37.0  | 14389 / 69.5 |

Cost repeats far better than accuracy. Over the 320 the two medium runs are 0.9% apart in
tokens and 0.9% in seconds, while their scores are 6 items apart. Per suite the cost can
still swing: humaneval 6516 against 4200 is the widest, the first run having spent more of
it on code-path rewrites.

Medians are well under the means everywhere: gsm8k R 5562 tokens and 42 s, D medium 3556
and 12 s, D high 2923 and 11 s; humaneval R 17642 and 91 s, medium 2129 and 10 s, high 2284
and 13 s; tools R 18085 and 93 s, medium 5796 and 36 s, high 22278 and 114 s. The means are
carried by the items that disagree and draw witnesses. "Forced" counts items where a
thinking call ran out of its budget and was made to answer (deviation 7): R 147 of 320
(the 12000 cap), D medium 98 of 370 (6000) and 95 on the repeat, D high 85 (16000).
"Capped" counts items that hit the per-item token cap between witnesses (deviation 15, now
on generated tokens, deviation 18b): medium 3 of 370 (humaneval 1, ocrbench 2) in both runs,
high 7 (humaneval 1, simpleqa 3, ocrbench 3); none of the 10 is right. The cap is checked between witnesses, so a capped item
still ends past it. Longest items: R 119 s, 167 of 320 over 60 s; medium ocrbench-281 at
237 s, 66 of 370 over 60 s; high simpleqa-237 at 500 s, 114 over 60 s.

On the 320 items the 9B runs, the two medium runs score 272 and 266 against the 9B's 268,
on 59% of its tokens and 55% of its seconds. High scores 267 on 105% of its tokens and
112% of its seconds. Three of those four numbers are within 2 of each other and the fourth
is 4 away, so on this set of items nothing separates the three conditions on score and the
whole argument for the split rests on cost. High is not ahead of medium on any suite in
the first run and is ahead on two in the second, which is the same statement. High costs
more than medium on every suite (gsm8k 1.5x the tokens, humaneval 1.3x, tools 2.4x,
multistep 1.7x, simpleqa 2.3x, ocrbench 1.4x): the thinking budget is 16000 instead of
6000, and a forced sample re-sends its thinking as prompt. Nothing in these six suites
needs more than 6000 tokens of thinking, so the extra budget buys a different draw rather
than a better one, and medium is the level to ship because it is the cheap one, not
because it scores higher. Simpleqa at high is the worst case,
226 s an item, because a closed-book item needs three samples in a row to agree and five
are drawn before it gives up.

Item by item. gsm8k: medium misses 18, R 16, 12 shared; medium alone misses 6 (1070, 353,
380, 611, 752, 943), R alone 4 (1071, 1166, 1185, 711). High misses the same 18 and 999 as
well, and gets nothing medium misses. 962 is wrong in every condition since v1. humaneval:
both levels miss 145, and high also 10. tools: R misses 21, 22, 23, 33 and 49, this runtime
none at either level. multistep: R misses 13, 14, 16, 18 and 27; medium misses 14, 31 and
37; high misses 20, 31, 37 and 39. simpleqa: R answers all 30 and is wrong on 25; medium
abstains on 25, answers wrong on 4, is right on 4 (three of them hedged); high abstains on
23, is wrong on 6, right on 2. ocrbench: 36 of 50 at both levels, the same 14 misses; the
second version at high 32, `pre-v3` 33.

One seed does not mean one token stream, and that matters for reading any gap in the tables
above. On the 260 items of gsm8k, humaneval and tools, the two levels end on the same answer
string 166 times and record different witness values 140 times. Of the 200 items where
neither level ran out of its thinking budget, 103 still drew different values, so the cause
is not the budget: llama-server runs four items at once, and batch composition changes the
floating-point reductions. Of those 140 items with different witnesses, 138 end on the same
verdict, which is the settle rule absorbing the difference.

So medium was run a second time to put a number on it: same commit, same box, same four
workers, same llama-server processes, nothing touched between the two (`5090-v3-witness`
finished at 18:30 UTC, `5090-v3-witness-2` started at 18:31). It scored 303 of 370 against
308, and 266 of the 320 against 272. Eleven items came out differently: eight went
right to wrong (gsm8k 161 and 450, HumanEval-10 and 125, multi-17, 26 and 39, simpleqa-627)
and three the other way (gsm8k-781, multi-14, ocrbench-175). Only 224 of the 370 ended on
the same answer string. Per suite the spread is tools 0, gsm8k 1, simpleqa 1, ocrbench 1,
humaneval 2, multistep 2.

That band is wider than most of the gaps this report would otherwise call findings.
The 9B's 268 sits between the two medium runs, so this runtime does not beat it on score;
high's 267 sits between them too, so the levels are not separated either. What survives the
band is cost, which repeats to within 1%, and two per-suite results: tools, 30/30 in both
runs against 25/30 with no item disagreeing between the runs, and the simpleqa hedging
split. Everything else here should be read as one draw, not a measurement. Three runs would
have given a standard deviation rather than a range; two was what the rented hours paid for.

How the witnesses behaved on gsm8k, the suite with enough items to say. Medium: the first
two (reasoning thinking, motor plain) agreed on 127 of 200 and were right on 119 of those
(93.7%); a third witness or more was drawn 73 times and those ended right 63 times; 5 items
ended with no majority and went out as the reasoning lobe's value with "not sure" in front,
and all 5 were right anyway. Of the 193 items settled on evidence, 175 are right (90.7%);
two settled on consistency, both right. Witness count per item: two on 127, three on 61,
four on 12. The reasoning lobe was called 285 times and motor 200; the verifier is called
zero times, because it stopped voting (deviation 18a). Motor's program printed nothing on
13 items. Forced out of the 6000-token thinking budget on 20 items, 14 of them among the 73
that drew a third witness. By pair: motor's program and the reasoning lobe's program settled
126 (116 right; 8 of the 10 wrong are wrong for the 9B too, leaving 943 and 752 as the two
the pair got wrong on its own); two reasoning programs 50 (44 right); a written reasoning
value and a reasoning program 9 (7); motor's program and a written value 8 (8); two written
values 2 (2); nothing agreed on 5 (5 right). Re-settling the recorded witnesses with only
the first two gives 175 and with the first three 179, against 182 as run: the hot samples
are worth 7 items against a first pair alone and 3 against the first three, and cost none.

High: the first two agreed on 133 (124 right, 93.2%); a third or more was drawn 67 times
(57 right); no majority 3 (all 3 right). Evidence 195 items, 176 right (90.3%); two settled
on consistency, both right. Two witnesses on 133, three 52, four 11, five 1, six 3. Motor
printed nothing on 11, forced 20. Pairs: two programs 124 (115 right), two reasoning
programs 50 (42), motor's program and a written value 11 (11), a written value and a
reasoning program 7 (6), nothing 4 (4). First two only 175, first three only 177, against
181 as run.

Images are the other place the pairs are worth reading. On ocrbench at medium the first two
(perception thinking on the image, then the reasoning lobe on the notes) agreed on 41 of 50
and were right on 32; the other 9 drew more witnesses and 4 of those ended right, and 7 of
the 9 never reached a majority and went out as perception's value, 2 of them right, which
is 36 of 50 in all. The split by pair is the argument for
deviation 18c: when the OCR engine's text backs perception's answer the pair is right 21 of
21, and when perception agrees with the reasoning lobe, which never sees the image, it is
right 12 of 21. Perception was called 50 times, the reasoning lobe 42, and the ocr witness
stood up 31 times. Re-settling on the first two or three witnesses changes nothing here.

Tools and multistep at medium: on tools the first two agreed on 28 of 30 and every one was
right, the other 2 drew a third and were right as well. On multistep the first two agreed on
12 (11 right), 18 drew more (16 right), 1 ended with no majority and was wrong; the thinking
budget was hit on 18 of 30 items, which is why multistep costs 14248 tokens at medium.
Re-settling multistep on the first two witnesses gives 23 and on the first three 25, against
27 as run, so the hot samples are worth 4 of the 30.

## Which hypotheses held

On the 90 items v4 added (PREREG-v4.md V1-V7, which share their names with PREREG-v2's V1-V5
and are a different set), seed 0, both medium runs. Three of the seven hold, one holds on a
technicality that is worth more than the hypothesis, and three fail.

- V1 (on the 30 new tools items, at least 10 points over the 9B, and a larger gap than on the
  old 30): holds, and it is the strongest result of the round. 30 and 28 against 11, a gap of
  19 and 17, against 5 on the old 30. The old suite was at the runtime's ceiling, not at its
  limit; that was the thing this round existed to find out.
- V2 (on the 30 new multistep items, at least 10 points over the 9B): fails. 25 and 28 against
  20 is 5 and 8 points. The gap is real against a band of 4 but it is not the 10 that was
  written down, and the difference between V1 holding and V2 failing says the win is running
  one program rather than chaining several.
- V3 (the band between the two medium runs, at most 8 items on the non-SimpleQA items): holds
  as measured, 4, but only the 90 new items were re-run, so this is a band on 90. The other 340
  records in each v4 file are carried and therefore identical by construction; they would have
  drawn their own noise had they been run again, and v3 measured that spread at 11 items on 370.
  Read V3 as untested at the width it was written for, and the 4 as the band on the new items,
  which is what every claim above it is checked against.
- V4 (tokens and seconds per item stay below the 9B on tools, multistep and gsm8k, and the two
  medium runs agree within 2% on the combined total): fails on the first half, holds on the
  second. On the new items this runtime costs 2.45x the tokens and 1.27x the seconds over tools
  and multistep together; only tools is under on seconds (50.8 and 57.1 against 54.4) and
  nothing is under on tokens. The repeat half holds again: 19990 against 20098 tokens is 0.5%
  and 80.5 against 81.4 seconds is 1.1%. Cost is still the only thing in this project that
  repeats to within a percent, and it now repeats around a number that is worse than the 9B's
  on hard items.
- V5 (on the new multistep items, a higher share where the witnesses agree on some values and
  not others): fails. Comparing the first two witnesses line by line, the old 30 split 8 all
  lines matching / 8 some / 14 none in the first run and 11 / 8 / 11 in the second; the new 30
  split 7 / 5 / 18 and 6 / 7 / 17. What rises on the harder items is not partial agreement but
  total disagreement, so the extra values per item do not give the contract more to work with;
  they give the witnesses more to diverge on.
- V6 (GSM8K stays within 2 points of the 9B): holds, carried. 182 and 181 against 184.
- V7 (this runtime at or above the 9B on AIME, and high above medium): fails on both halves.
  Medium scores 12 and 15 against the 9B's 13, so the 9B sits inside the medium spread; high's
  16 is inside the spread of medium's three samples (16, 12, 15) at 1.97x the tokens. This was
  the one item set where the effort knob was expected to have something to separate, and it is
  the third pre-registration in a row where it separates nothing.

On the 5090, this runtime against the 9B alone (PREREG-v3.md W1-W8), seed 0, at medium
unless said. Four of the eight fail. The verdicts below are read off the first medium run;
the repeat run was checked against every one of them and changes none, though W1, W2 and
W8 are inside the run-to-run band and hold only in the sense that neither run breaks them.

- W1 (multistep and tools each at least 10 points over R, and this runtime the right one
  more often when exactly one is right): fails on the first half. tools holds, 30 against
  25 is 16.7 points, and the five items where exactly one is right are all this runtime's.
  multistep is 27 against 25, 6.7 points, short of 10; its second half holds, the six items
  where exactly one is right split four to two in this runtime's favour. The repeat run has
  tools at 30 again and multistep at 25, level with R, so the tools half is the only part
  of W1 that is outside the noise.
- W2 (gsm8k not below R minus 2 points; 1252 and 413 right): holds. 182 against 184 is
  1 point down, 181 on the repeat is 1.5 down, and 1252 and 413 are right at both levels
  and in both runs, all on evidence.
- W3 (tokens and seconds below R on gsm8k, multistep, tools and simpleqa; below
  `5090-v2-high` on every suite): the first half holds on all four suites, tokens and
  seconds both. The second half fails: on the items the two runs share (the first 30 of
  gsm8k, humaneval, simpleqa, the first 20 of ocrbench; the tools and multistep items were
  replaced), gsm8k is 5558 against 5024 and simpleqa 19841 against 15286; humaneval 6516
  against 8697 and ocrbench 11484 against 16773 are below.
- W4 (no item exceeds the medium caps, at most 2% hit one, longest under 60 s): the
  2% half now holds, 3 of 370 is 0.8% and the repeat run capped the same 3, down from 27
  when the cap counted re-sent thinking (deviation 18b). The other two fail: the cap is checked between witnesses, so all 3 ended
  past it, and the longest item is 237 s with 66 items over 60 s under four workers.
- W5 (first two witnesses agreed are right 97% of the time, disagreement under 25%):
  fails on both. The first two agreed on 127 of 200 (disagreement 36.5%) and were right on
  119 (93.7%). The mechanism, as the prereg asked: of the 8 agreed-and-wrong items, 7 are
  wrong for the 9B too (962, 823, 1042, 1016, 494, 12, 403), so those are the question read
  the same way by every model, gold-disputed or a standard misreading, not a shared bug of
  the two programs; the remaining one is 752, motor's program agreeing with the reasoning
  lobe on the tail of a longer printout. The witnesses are independent on arithmetic and on
  code slips and not on how they read the words, because they read the same words. At high
  the first two agree on 133 (disagreement 33.5%) and are right on 124 (93.2%), still short.
- W6 (ocrbench at or above `pre-v3` D, and 5 points above the single 4B on the first 20):
  holds at both levels, 36 against 33, 37 on the repeat, and 14 of 20 against 11 of 20,
  which is 15 points.
- W7 (simpleqa unsupported not above `pre-v3` D, confident correct not below it by more
  than one): holds, unsupported 4 against 18 and 5 on the repeat, confident correct 1
  against 2 in both runs.
- W8 (humaneval within 2 items of v2 D high, 26/30): holds, 29, 27 on the repeat, and 28
  at high; the repeat is at the edge of the window.

The earlier hypotheses, on the runs in the appendix below.

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
16. 09-15 afternoon, after the numbers above were written up: the witness's program field was
    called `check`. Counted over the medium traces, 90 of 765 programs defined a `check()` and
    never called it and 88 more printed nothing (high: 108 of 845); each cost a repair call and
    a run and left the witness with only the values it wrote down. The field is now `program`
    and the instruction says top-level statements. Both levels ran again with that one change
    (`5090-v3-program`, `5090-v3-program-high`); the earlier `5090-v3` and `5090-v3-high` stay
    on disk for the comparison. The README and the v3 tables below are from the runs of
    deviation 18, which came after.
17. The blind test on code without examples, measured on the same traces while the reruns
    were going: 16 blind tests on 13 items at medium, 18 on 14 at high. 21 of the 34 defined
    the function under test again inside the test, so the test exercised the verifier's own
    implementation and never the candidate: 11 fake PASS with basis evidence, 7 RETRY that
    blamed a right candidate for the verifier's own asserts (HumanEval-117 burned all four
    retries at high this way), 3 RETRY on wrong candidates for the same non-reason. 3 more did
    not parse (`'\n'` inside a JSON string comes back as a newline; HumanEval-125 went out
    untested at both levels). Of the 10 genuine tests, 8 gave the right verdict and 2 failed
    right code (HumanEval-83, both levels). Stripping the re-implementations does not rescue
    it: the verifier's asserts then fail right code 10 of 25 times (5 of 13 at medium, 5 of 12
    at high). Over the 4 wrong first candidates (125 and 163 at each level) it caught none on
    its own merits; the one fix (163 at high) was a RETRY whose test had failed its own
    implementation. Removed: no examples in the task, no check, the code goes out as basis
    none. The 30 humaneval items of both program reruns were then run again with that code
    into the same result files (the code path is only ever entered on humaneval, checked over
    every trace); the first pass of those 30 is kept next to them as humaneval-blind-test.jsonl
    and the other suites' records stand.
18. 09-15 evening, from the shipped medium and high traces, before any new run: (a) the verifier
    model as a blind third witness was right 6 of 68 times at medium and 11 of 75 at high on
    gsm8k; every motor+verifier pair that settled an item was wrong (10 of 10 over four runs),
    and 3 of those at medium overrode a right reasoning value (gsm8k-1267, 450, 1032). It no
    longer votes; the plan on a computable task is reasoning, motor, then hot samples of
    reasoning. (b) The per-item token cap compared total_tokens, which counts a forced answer's
    re-sent thinking twice: at medium a single forced sample cost 12.7k of the 16k, so four
    items whose second reasoning sample was right (340, 506, 1070, 611) were hedged before a
    third could be drawn. The cap now counts completion_tokens; the table values stand. (c) On
    ocrbench the reasoning lobe, which reads the perception notes and never the image, was
    right 12 of 50 at both levels, and two of its samples agreeing (or one backed by the ocr
    text) overrode a right perception read 3 times at each level (359, 281, 176). A pair that
    settles an image task must now include perception, and the hedged fallback on an image is
    perception's value. (d) Code examples written as `f(x) => y`, `f(x) ==> y`, `f(x) ➞ y` or
    `f(x) == y` in prose, and `>>> f(x) == y` lines with no expected output, were not examples
    to the checker: 9 of the 30 humaneval items have only those (5 more have none it can read),
    and HumanEval-145's two `>>>` lines failed every candidate (empty want). They are read now,
    as `f(x) == y` expecting True, when the right side parses as a literal. (e) Measured
    on the 5090 before the reruns, the 2B perception lobe asked five ways on the 50 ocrbench
    images: plain 32, plain again 31, at temperature 0.7 33, transcribe first 29, thinking
    34 of the 45 the server completed; the reasoning lobe on the notes 31 of 48. Replayed
    through the settle rule, perception thinking first scores 35 whoever follows, the shipped
    order 32. Perception now thinks as the effort says, the same rule the reasoning lobe
    already follows on text. The 5 refused calls were the 2B's four server slots sharing one
    16384-token context under four workers; a thinking call the server refuses is asked once
    more plain (the trace records `refused`). (f) A docstring want that is a literal is compared
    as a value: HumanEval-65 writes "21" where repr says '21', and right code failed both of its
    examples in every run (it still went out, basis none). (g) The executive called all 30
    humaneval items code, and 5 multistep and 3 tools items too, so its call alone cannot route;
    on HumanEval-117 and 106 the reasoning lobe then wrote the function as its value instead of
    in the code field, witnesses can never agree on source, and the hedge prefix turned the
    answer into a SyntaxError. A value that is source, on a task the executive called code, now
    takes the code path, and a hedge never goes in front of code.
    All seven are mechanism changes measured on the traces; none reads a benchmark's answer
    format. Both levels run again under `5090-v3-witness` and `5090-v3-witness-high`, plus a
    second medium run of the same code (`5090-v3-witness-2`) to put a number on run-to-run noise.
19. In the high run, 9 of the 50 ocrbench items came back as a 500 from llama-server instead
    of an answer: the 2B perception child serves four slots out of one 16384-token context, and
    an image plus a 16000-token thinking budget times four does not fit (the same overflow as
    deviation 18e, which the plain retry covers only when the server refuses the call rather
    than erroring). The medium run, whose budget is 6000, lost none. Those 9 records were moved
    to `ocrbench-500-errors.jsonl` next to the results and the suite was run again with the same
    code and the same four workers; `lobes eval` skips items already recorded, so only the 9 ran.
    Every ocrbench number for high is from that pass. No other suite was affected and nothing
    was re-rolled: the 41 items that completed the first time are the first time's records.
20. PREREG-v4 says the AIME pilot runs 10 to 15 items before the suite is frozen. All 30 ran.
    `lobes eval` is resumable and works item by item, so stopping it at 12 would have thrown
    away work already paid for, and the stopping rule was "drop the suite if it scores 0", which
    12 items answer as well as 30. It scored 16 of 30, so the suite went in. The pilot records
    are in `eval/results/5090-v4-aime-pilot` and are a pilot, not one of the four v4 runs; they
    are read once in this report, as the third medium sample of AIME.
21. The first v4 launch ran all six suites on both boxes, 430 items per condition, and was
    stopped 12 minutes in. Re-running GSM8K, HumanEval, OCRBench and the old halves would have
    drawn a second sample of a distribution v3 already sampled twice at medium, and bought
    nothing this round is about. The partial files are kept next to the real ones as
    `5090-v4-aborted-fullsuite` and `5090-v4-high-aborted-fullsuite`; nothing was read off them.
    About 15 minutes of machine time on two boxes went into them.
22. What replaced it: each v4 result file was seeded with the carried records before the run
    started, tagged `carried` with the run they came from, and `lobes eval` skipped them by
    suite and id the way it resumes any interrupted run. Each condition carries from its own v3
    counterpart, never across conditions: the medium file from `5090-v3-witness`, the repeat from
    `5090-v3-witness-2`, high from `5090-v3-witness-high`, the 9B from `5090-v3`. The runtime is
    the same code in both rounds, `git diff aded056 2bb09e1 -- lobes/` being two docstrings in
    `reasoning.py` and `verifier.py`, so a carried record and a re-run one differ only by the
    run-to-run noise measured above. What each v4 run actually executed is the 90 new items.
    The carried numbers are v3 numbers and are labelled as such wherever they appear.
23. The 9B ran the new items twice. On the first pass 47 of the 90 came back as a 500 from
    llama-server, `Context size has been exceeded`: condition R sends one long chat call and the
    9B's child server was serving four slots out of one 16384-token context, and unlike the
    runtime path, `eval.raw_item` has no retry without thinking to fall back on (the same
    overflow as deviations 18e and 19, in the one place with no cover for it). The 9B's context
    was raised to 65536 and the suite run again. Raising it needed the router restarted, not
    just `models/models.ini` rewritten: `llama-server --models-preset` reads the preset once at
    startup, so editing `lobes.yaml` and regenerating the file changed nothing until `lobes serve`
    and every child were killed and started again. The failed pass is kept as
    `R-s0-ctx16384-500s.jsonl` next to the results. This leaves a blemish worth stating: in the
    9B's v4 file the 290 carried records ran at a 16384-token context and the 90 new ones at
    65536. The carried numbers are the ones v3 reported and are unchanged by this; the new 90
    are the only ones the larger context touched, and they are the ones being compared.

## Earlier versions

Kept for the record; every table here was written when its run finished and has not been
edited since. The conditions (A to F) and the v1 / v2 names are those of PREREG.md and
PREREG-v2.md. The 4070 numbers are on the small suites of PREREG.md, the 5090 v1 / v2 tables on
the same small suites (deviation 9), and the last table on the enlarged suites of PREREG-v3.md.

### Quick timing on the 4070 (seeds 0-2 mixed, 3 items per suite, not part of the results)

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

### Every full run on the enlarged suites

Seed 0, four items in flight, the 320 items the 9B runs and all 370; tokens and seconds are
means per item on the 320. The bare 9B: 268 of 320, 13,436 tokens and 62.2 s an item.

| results directory | what it is | of 320 | of 370 | tokens | s |
|---|---|---|---|---|---|
| `5090-pre-v3` | the second version at medium with its 1.2B classifier, as pre-registered | 234 | 267 | 5,081 | 25.8 |
| `5090-v2fix-high` | the second version at high with the model-only intake, branch `v2-fix` (deviation 12) | 241 | 273 | 17,494 | 61.1 |
| `5090-v3-old-intake` | witnesses, medium, before the intake fix (deviation 10) | 245 | 277 | 8,698 | 44.2 |
| `5090-v3-last-number` | medium, agreement on the last number only (deviation 13) | 257 | 287 | 8,873 | 38.4 |
| `5090-v3` | medium, the contract of deviations 13 to 15 (09-15 noon) | 261 | 291 | 8,981 | 37.7 |
| `5090-v3-high` | the same at high | 267 | 297 | 14,375 | 65.0 |
| `5090-v3-program` | medium, the program field (deviation 16); humaneval rerun without the blind test (17) | 250 | 280 | 7,677 | 33.1 |
| `5090-v3-program-high` | the same at high | 260 | 290 | 14,369 | 69.3 |
| `5090-v3-witness` | medium, deviation 18: the README numbers | 272 | 308 | 7,887 | 34.0 |
| `5090-v3-witness-high` | the same at high (deviation 19) | 267 | 303 | 14,160 | 69.5 |
| `5090-v3-witness-2` | medium once more, same code, for the run-to-run noise | 266 | 303 | 7,813 | 34.3 |

The v4 runs are not in that table because they do not run those 320 items; they run the 90
items v4 added and carry the rest. Seed 0, four items in flight, means per item on the 90.

| results directory | what it is | of 90 | tokens | s |
|---|---|---|---|---|
| `5090-v4-aime-pilot` | AIME only, medium, the pilot that froze the suite (deviation 20) | 16 of 30 | 29,827 | 121.6 |
| `5090-v4` `R-s0` | the 9B alone, second pass at a 65536 context (deviation 23) | 44 | 6,189 | 48.7 |
| `5090-v4` `D-s0` | medium, the README numbers | 67 | 19,990 | 80.5 |
| `5090-v4-2` | medium once more, same code and box, for the band | 71 | 20,098 | 81.4 |
| `5090-v4-high` | the same at high | 74 | 41,280 | 189.7 |

## Things I noticed while judging

- The multistep judge is "every expected value appears in the answer". One A item answered
  with a truncated JSON envelope (the 9B hit the token cap mid-output) and still passed
  because "210" occurred in the text. The judge is fixed by PREREG; I list such cases here
  rather than re-judge them.
- SimpleQA correctness is string containment, a lower bound. The number that matters there
  is the unsupported rate (answered and wrong).
- OCRBench gold for some items is non-Latin text; the 4B produced mojibake on one, counted
  wrong.
