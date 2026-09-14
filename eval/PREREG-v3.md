# Pre-registration, v3

Written 2026-09-14 after v2 was frozen and before any v3 run. The 5090 v1 and v2 runs
(PREREG-v2.md) were still in progress, so nothing here reacts to v2 numbers. Items 1-9
are in PREREG-v2.md; the numbering continues.

## What changed since v2

10. reflect: at high and above, the reasoning lobe gets its own draft back with the goal
    and the observations and is asked for one concrete mistake (REFLECT_SYS in
    lobe/reasoning.py). A named flaw with a changed answer replaces the draft; the
    change is written to the trace and to the final uncertainties. Drafts a tool
    printed, that passed their `>>>` examples, or that every vote sample agreed on are
    not reflected on. medium is untouched.
11. search: at high and above, the verifier scores `width` candidates per step (the
    solve() draft plus width-1 samples at 0.7) and the highest stays. The score is the
    verdict as a number: PASS on evidence 3, on consistency 2.5, on nothing 2,
    VERIFY_WITH_TOOL 1, anything else 0. Ties go to the first candidate. Vision tasks
    skip it. width is 2 at high, 3 at xhigh, 4 at max.
12. auto: `--effort auto` starts at medium and, each time the retries run out, climbs
    to high and then xhigh before the model ladder (escalate, remote) takes over. Every
    result record carries `effort` (requested) and `level` (where the answer came from).

## Hypotheses, decided now

W1. D at high, v3 vs v2, same seed and items: gsm8k, humaneval and tools each up by at
    least 3 points, no suite down by more than 5. If the traces show reflection changing
    more right answers to wrong than wrong to right on a suite, it is turned off for
    that task class and the report says so.
W2. D at auto (v3) vs D at medium (v2): mean over suites up by at least 3 points at no
    more than 1.5x the tokens per item. If this holds, auto becomes the default in
    lobes.yaml; if not, medium stays.
W3. From the traces: reflection changes the answer on 5-20% of high items and more of
    those changes go wrong-to-right than right-to-wrong. Search keeps a candidate other
    than the first on at least 10% of items, and those items are right more often than
    the first candidate alone would have been (the trace has every candidate's answer).

## Conditions to run

After the runs in PREREG-v2.md: D at high, seed 0, tagged `5090-v3-high`, and D at
auto, seed 0, tagged `5090-v3-auto`, both on v3 code. v2 vs v3 at medium is not run:
10-12 do not touch medium.
