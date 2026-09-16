# Lobes architecture

[中文](ARCHITECTURE.md) | English

This document describes the current design. The reasons behind earlier versions are in [`DECISIONS.md`](DECISIONS.md), and benchmark numbers are in [`../eval/REPORT.md`](../eval/REPORT.md).

Lobes is built around one idea: small models are only useful as a system if they receive different information or perform different kinds of work. Chaining several similar prompts together mostly compounds errors.

## Machine and backend

The main local target is an RTX 4070 Laptop GPU with 8 GB VRAM. `lobes.yaml` currently reserves about 6.4 GB for models because the desktop already uses part of the card.

Inference runs through `llama.cpp` / `llama-server`. GPU models are loaded and unloaded on demand. The executive classifier stays on CPU and does not participate in GPU swapping.

The evaluation runs used rented RTX 5090 machines so several requests could run in parallel without spending most of the benchmark time swapping models.

## Lobes

| lobe | role | default implementation | location |
|---|---|---|---|
| executive | classify the request; decide whether a program is useful | Granite 4.0 H 1B Q8 | CPU, resident |
| perception | read images and answer image questions | Qwen3.5-2B + mmproj | GPU |
| reasoning | main reasoning witness; write code and recomputation programs | Qwen3.5-4B Q4 | GPU |
| motor | produce a program directly from the request; stdout becomes a witness | Granite 4.0 H Micro 3B Q4 | GPU |
| verifier | run examples and deterministic checks | Python code | CPU |
| language | turn settled values into a normal answer without changing them | Gemma 4 E2B Q4 | GPU |

Model names live in [`../lobes.yaml`](../lobes.yaml). The runtime code works with lobe names and provider/model references instead of hardcoding a model family.

There is also a `shared` profile where several lobes reuse one Qwen3.5-4B model with different prompts. The single-model profiles are used as evaluation controls.

## Why the executive only classifies

Earlier versions let the executive produce a plan and then showed that plan to the other lobes. That made the whole system inherit the executive's interpretation of the task. If the first model misunderstood one clause, the other models often repeated the same mistake.

The current executive therefore does much less: it classifies the request and decides whether computation is useful. It is not a witness and does not decide the final answer.

## Witnesses

The system does not use one model as a judge over another model. Instead, it tries to get independent witnesses.

A witness receives the original request. For image tasks it may also receive observations from perception and OCR, with the source labelled. It does not see another witness's plan, program, or answer.

A witness returns values in a simple contract: one requested value per line, in request order. A program-backed witness also records that its value came from actual execution.

Two matching witnesses can settle an answer. Once a program has run successfully, a pair of model-written values cannot outvote the program output. Closed-book questions require repeated independent agreement because there is no external evidence to check.

## Request flow

```text
user
  |
  v
executive
  |-- chat ------------------------------> short answer
  |-- image --> perception + OCR --------> witnesses
  |-- code requested --> reasoning ------> run examples when available
  |-- computable --> reasoning + motor --> independent programs / values
  `-- closed book --> reasoning samples -> consistency check

settled values -> language -> reply
unsettled values -> best available answer + uncertainty marker
```

### Computable requests

The reasoning lobe thinks through the problem and also writes a small recomputation program. The motor lobe sees the original request independently and writes its own program. Program output is used as the value instead of trusting the prose around it.

If the programs or values disagree, the runtime can request more reasoning samples until the effort budget is exhausted.

### Code-generation requests

When the user wants source code, reasoning produces the implementation. If the prompt contains runnable examples, the verifier runs those examples. A failed candidate can be rewritten with the failure attached, up to the repair budget for the selected effort level.

If there are no usable examples, the runtime does not pretend it verified the code.

### Image requests

Perception reads the image. RapidOCR can provide a second, non-LLM reading. Because only perception actually sees the pixels, an image answer cannot be settled only by several reasoning samples that all read the same notes.

## Deterministic verifier

The verifier used to be another model. That did not work well: a smaller model often either approved a stronger model without adding evidence or introduced another guess.

The current verifier is mostly plain code. It handles things that can be checked exactly:

- run examples included in a programming prompt;
- compare witness values;
- match values against OCR text;
- preserve settled values through the language rewrite;
- track whether evidence came from execution or only from consistency.

This change made the system simpler and easier to reason about.

## Effort levels

`low`, `medium`, `high`, `xhigh`, `max`, and `auto` scale several things together: thinking budget, number of witnesses, repair attempts, and per-request caps.

| level | thinking | think tokens | reasoning samples | repairs | witness cap | call cap | token cap | time cap |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| low | off | 0 | 1 | 1 | 4 | 8 | 6,000 | 120 s |
| medium | on | 6,000 | 3 | 2 | 8 | 16 | 16,000 | 300 s |
| high | on | 16,000 | 5 | 3 | 12 | 24 | 40,000 | 600 s |
| xhigh | on | 32,000 | 8 | 4 | 18 | 36 | 80,000 | 1,200 s |
| max | on | context limit | 12 | 6 | 30 | 60 | none | none |

`auto` starts at medium and only moves upward if the current witness budget is exhausted without settling the answer.

The benchmark so far does not show a general benefit from high effort. On the main evaluation, high stayed inside the medium score spread while using much more compute.

## VRAM strategy

The 4070 target cannot keep every GPU model resident at once, so the model manager uses a VRAM budget and LRU-style swapping. The executive is CPU-resident and never evicted.

The main GPU path is roughly:

```text
reasoning -> motor -> language
```

The exact measured model sizes and swap timings are kept in the Chinese architecture notes and `lobes.yaml`. They are implementation details rather than assumptions of the runtime.

## Data passed between modules

The main structured objects are:

```text
Observation  source + reference + summary
Witness      lobe + value + whether execution produced it + reference
Verdict      pass / retry / conflict + evidence basis + notes
Envelope     request kind + answer + uncertainties + confidence + next action
```

The schemas live in `lobes/schema.py` and are also exported as JSON schemas for constrained decoding.

## Processes

A normal local run has two main processes:

1. `llama-server`, which owns model loading and inference;
2. the Python Lobes process, which owns routing, tools, verification, CLI, and API behavior.

`lobes api` exposes an OpenAI-compatible `/v1/chat/completions` endpoint, so another client can treat the whole Lobes runtime as one model.

## Safety note

The current Python and shell tools are intentionally not sandboxed. They run with the permissions of the current user and only have a timeout as a guardrail. File-specific tools are restricted to the run directory, but Python and shell are not.

For untrusted workloads, the whole runtime should be placed inside a container or disposable environment.

## What is still experimental

The architecture is still changing. In particular:

- there is no multi-turn memory yet;
- the 8 GB swapping setup serves one request at a time;
- closed-book factual recall is still weak;
- the same-tool 9B ablation has not been run yet;
- some design choices have only been tested on the current benchmark distribution.

The project keeps these limitations visible because the point is to test the architecture, not just produce a good-looking score.
