# Pre-registration

Written 2026-09-14 before any eval run. Not edited after the runs start; anything I
change my mind about goes into REPORT.md as a deviation.

## Hypotheses

H1. On raw accuracy, the specialists profile (5 small models, 4B core) does not beat a
    single Qwen3.5-9B with the same scaffolding. Expected: A >= D on every suite.
H2. What modularity buys is reliability and cost control:
    - fewer stuck loops and lower variance across seeds on the multi-step suite (D, E vs A, B);
    - a lower unsupported-claim rate on SimpleQA (the verifier / abstention path), even if
      accuracy is not higher;
    - better accuracy per 1k tokens and per second than the 9B.
H3. Escalation (E) recovers most of the gap between D and A on GSM8K and HumanEval at a
    fraction of A's token cost, because only the failed items pay for the 9B.
H4. Equal-compute voting with the single 4B (B3: three samples, majority) does not reach
    D on the tool and multi-step suites. If it does, the modular pipeline is not earning
    its complexity there.

Sharing one model across lobes (C) is the control that separates "different prompts per
phase" from "different models per phase". If C ~= D, the models are interchangeable and
the win, if any, comes from the scaffolding.

## Conditions

All conditions use the same runner, tools, grammar constraints, retry budget (2) and
step cap (10). Only the profile and the escalation ladder change.

| id | profile        | escalation | notes |
|----|----------------|------------|-------|
| A  | single-9b      | off | Qwen3.5-9B IQ4_XS in every slot, no perception (no mmproj) |
| B  | single-4b      | off | Qwen3.5-4B (vl) in every slot |
| B3 | single-4b      | off | same, reasoning samples 3 at temp 0.7 and votes from the first step |
| C  | shared         | off | 4B-vl for perception/reasoning/motor/verifier, lfm executive |
| D  | specialists    | off | one model family per lobe |
| E  | specialists    | on  | D plus the 9B rung; the remote rung only if DEEPSEEK_API_KEY is set |
| F  | remote         | off | deepseek-chat in every slot, skipped without a key |

## Suites

Fixed subsets: the first n items of each source after a shuffle with seed 20260914.
Item order and ids are written to eval/data/*.ids.json on the first run and reused.

| suite     | source                                   | n   | judge |
|-----------|------------------------------------------|-----|-------|
| gsm8k     | openai/gsm8k main/test                   | 50  | last number in the answer == number after "####" (rel 1e-6) |
| humaneval | openai/openai_humaneval test             | 30  | answer + test + check(entry_point) exits 0 in 10 s |
| tools     | eval/suites/tools.jsonl (mine)           | 20  | expected string or number appears in the answer (normalized) |
| simpleqa  | simple_qa_test_set.csv                   | 50  | gold string appears in the answer (normalized); abstain regex |
| ocrbench  | echo840/OCRBench test                    | 20  | any gold answer appears in the answer; conditions with perception only (B, B3, C, D, E) |
| multistep | eval/suites/multistep.jsonl (mine)       | 10  | every expected value appears in the answer; run with 3 seeds |

SimpleQA's official grader is an LLM. Mine is a string match, so "correct" is a lower
bound and the number I care about there is the unsupported-claim rate:
unsupported = answered (not abstained) and wrong. Abstained = the answer matches
/don't know|do not know|not sure|cannot (find|determine|verify)|no (reliable )?information|unknown|unable to/i.

## Metrics per item

correct, abstained, tokens (prompt, completion, total), ms end to end, ms per lobe
(sum of that lobe's calls), swap count and swap ms, VRAM peak (nvidia-smi sampled
every 100 ms in a thread), steps, retries, escalations, verdict basis of the last
verdict, stuck (hit the 10-step cap without a PASS).

Per condition and suite: accuracy, accuracy per 1k total tokens, accuracy per second,
stuck rate, mean and std of accuracy across seeds (multistep), unsupported rate and
abstention rate (simpleqa).

## Thresholds, decided now

- H1 holds if A - D >= 0 on gsm8k and humaneval. A 5-point lead for D on either is the
  surprising result.
- H2 holds if D's stuck rate on multistep is <= half of A's or B's, and D's unsupported
  rate on simpleqa is >= 10 points lower than B's. Accuracy per second and per 1k tokens
  are reported, not thresholded, since the 9B is a different quantization.
- H3 holds if E closes >= 50% of the A - D gap on gsm8k and humaneval while spending
  <= 60% of A's total tokens on those suites.
- H4 holds if B3 is >= 5 points below D on tools and multistep.

## Seeds

Seeds 0, 1, 2. Every condition runs seed 0 on the full n. Seeds 1 and 2 run the
multistep suite in full (that is where variance is measured) and the first 10 items of
every other suite. The seed goes to llama-server as the sampling seed and to the run
id, nothing else changes.

## Budget and cut rule

The whole thing has to finish in one night (8 h). Item counts above give roughly
180 + 2 x 60 = 300 runs per condition, 1800 total at about 15 s each = 7.5 h before
condition F, which is skipped without a key. Before the full run I time `lobes eval
--quick` (3 items per suite, seed 0, all conditions) and if the projection exceeds 8 h
I cut in this order, from timing alone, before looking at any accuracy:

1. seeds 1 and 2 keep multistep only;
2. gsm8k and simpleqa go to 30;
3. B3 is dropped.

Whatever is cut is listed in REPORT.md.

## What I will not do

Change prompts, thresholds, judges or n after the first full run starts. Rerun a
condition because it looked unlucky. Drop items that all conditions fail.
