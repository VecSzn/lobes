# Pre-registration, v2

Written 2026-09-14 after the v1 run on the 4070 had finished A and D and half of E, and
before any v2 run. v1 is the code at tag `v1-4070`; v2 is what follows it on main.
Same suites, same item ids, same judges, same n as the cut v1 plan (gsm8k 30,
humaneval 30, tools 20, simpleqa 30, ocrbench 20, multistep 10 x 3 seeds). Not edited
after the v2 runs start; changes of mind go into REPORT.md as deviations.

Both versions are run on the same rented machine (RunPod, one RTX 5090 32 GB), because
the 4070 numbers carry swap time that the 5090 does not, and because the v1 run there
is the only way to separate "the code changed" from "the machine changed".

## What changed, and the v1 failure each change answers

1. python tool: code that does not compile is retried with `\n`, `\t`, `\"` unescaped
   if that version compiles. In v1, 43% (A) and 37% (D) of python calls died on one
   SyntaxError because the motor lobe writes newlines as a literal backslash-n.
2. verifier, code tasks: the task's own `>>>` examples run first, in code. Only a task
   without examples gets a model-written test, and the model writes it without seeing
   the code (it gets the defined names). A test that crashes inside its own lines is
   discarded, not counted against the candidate. Answers that are prose or a bare
   number are not code and go to the blind re-solve. In v1, 5 of D's humaneval misses
   were "verifier wrote no test, PASS", 4 were weak tests passing wrong code, and two
   tools items PASSed a wrong number by running it as a program.
3. verifier, conflicts: when the blind re-solve disagrees and nothing is left to run,
   an answer that appears in a successful tool's stdout wins, whichever side produced
   it. If it is the blind one, it replaces the candidate (Verdict.answer). tools-09 in v1
   ended on CONFLICT holding a wrong candidate while the verifier had the tool's answer.
4. evidence: a numeric answer while every python call failed is a RETRY once, with the
   note to fix the code and run it again (tools-02).
5. reasoning: a thinking call that returns no JSON is re-asked without thinking. The
   v1 A/simpleqa items at 160 s were 6000 tokens of thinking with no answer.
6. reasoning, sampling: code tasks with examples run the examples on the first sample
   and only draw two more (0.7) if it fails, keeping the one that passes most.
   Closed-book qa (no tools, no images) always draws three; unanimous agreement gives
   basis consistency, anything less gives none. No other task samples more than once
   before a retry.
7. perception: images with a short side under 768 px are upscaled before encoding;
   RapidOCR (code, optional extra) reads every image and lands as a tool output the
   verifier and the claims can cite; the verifier asks the perception model the
   question directly as a second look. Two of the three readers agreeing is a PASS.
   All 11 of D's ocrbench misses in v1 were misreads, mostly on small crops.
8. language: an answer whose last verdict is not PASS, or PASS on nothing, is prefixed
   "Not sure. Best guess:". The v1 abstain regex matches it, so the judges are
   untouched and hedged answers count as abstained. The report adds "confident
   correct" (correct and not abstained) next to the v1 "correct".

## Hypotheses, decided now

V1. Each change holds on the suite it was made for: tools and humaneval up for D (2,
    3, 4), ocrbench up for D (7), simpleqa unsupported down for D (8), and no other
    suite of the same condition drops by more than 5 points. A change that fails this
    is reverted, and the report says so.
V2. With the same scaffolding applied to both, specialists (D) reaches the single 9B
    (A) on tools and multistep and stays within 5 points on gsm8k and humaneval.
V3. D ocrbench >= 65 (v1: 45). Sampling and the ocr reader carry most of it.
V4. D simpleqa unsupported <= 20% (v1: 63%) and confident correct not below v1 correct
    minus 3 points. Hedging trades a right guess for a smaller wrong rate; the wrong
    rate is the number that matters on this suite.
V5. v1 on the 5090 reproduces v1 on the 4070 within 5 points per suite for A and D.
    If it does not, the comparison v1 vs v2 uses the 5090 numbers only.

## Conditions to run

A, D, E at `v1-4070` and at v2 on the pod, seed 0 in full and seeds 1-2 multistep as
in the cut plan. B and C at v2 if time allows. F only with a key. Results go to
`eval/results/5090-v1/` and `eval/results/5090-v2/` (the `--tag` option); the 4070 run
stays in `eval/results/`.
