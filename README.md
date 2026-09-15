# Lobes

Six small models, one job each, swapped in and out of an 8 GB GPU. An answer is
accepted when two derivations that never saw each other agree, and what a program
printed counts for more than what a model wrote.

I came up with this on my own, then went reading and found the neighbours: a
controller model handing sub-tasks to specialists is HuggingGPT, trying a cheap model
before an expensive one is a cascade (FrugalGPT), and every multi-agent framework
gives each role its own model. What almost none of them run is the control, one model
given the same scaffolding, so what they show is that the scaffolding helps, not that
the split does. So the question here is narrower. Does splitting the work across
small models from different families buy anything over one 9B, or one 4B, or one 4B
playing all six parts, each with the same tools, retries and verifier? The 9B with no
scaffolding at all is the floor. My bet, written down before running anything
([eval/PREREG.md](eval/PREREG.md)): not accuracy. Maybe reliability and cost. The
numbers are in [eval/REPORT.md](eval/REPORT.md) and summarized below.

Design notes are in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) (Chinese),
what changed and why in [docs/DECISIONS.md](docs/DECISIONS.md).

## What is in it

    executive    picks the route (rules first, a 1.2B model for the rest); writes no plan
    perception   describes images and screenshots; an ocr engine reads them a second time
    motor        first witness on anything computable: one tool call from the goal alone,
                 its output is the value
    reasoning    second witness: thinks, answers, and hands over a program that recomputes
                 the answer; the program's output is the value. Code tasks: the
                 implementation, re-done with the failure attached when its tests fail
    verifier     third witness from another family when the first two disagree; for code,
                 the task's own >>> examples or a test written without seeing the code
    language     final wording; a code check stops it from changing numbers or dropping a
                 line, and an answer nothing backs gets a "not sure" in front

Each witness gets the goal (and, for images, what perception and the ocr engine read,
labelled) and nothing another witness produced. Two that agree settle it: `evidence`
when one of them ran a program, `consistency` when neither did. No majority within the
level's witness count means the reasoning lobe's value, hedged. There is no retry loop;
a program that dies gets one repair with its own stderr and that is all.

Which model fills which lobe is only in `lobes.yaml`. A lobe can point at a local
GGUF (llama-server in router mode), an OpenAI-compatible remote, or plain code.
Profiles: `specialists` (one family per lobe), `shared` (one 4B for everything),
plus the single-model controls used by the eval.

Tools: python, shell (timeout, blacklist), read/write/edit file, web fetch,
screenshot. Every tool output is a file in `runs/<task>/`; it reaches the answer only
as a witness's value, never as text another lobe reads.

## Quick start

NVIDIA GPU, Python 3.11+. Windows gets the llama.cpp release binary; Linux builds
llama-server from source (git, cmake, nvcc on PATH).

    pip install -e .
    lobes install                   # llama.cpp + the GGUFs for the default profile
    lobes serve                     # keep this running
    lobes ask "what is 17 * 23"
    lobes ask --image shot.png "what is on this screen"
    lobes ask --effort high "..."   # low | medium | high | xhigh | max | auto, see below
    lobes api                       # OpenAI-compatible /v1/chat/completions on :8090

    curl http://127.0.0.1:8090/v1/chat/completions -d '{"model":"lobes/specialists","messages":[{"role":"user","content":"what is 19 * 21"}]}'

`lobes models` shows what is loaded and what it costs. `lobes ask --lobe reasoning --schema`
talks to one lobe directly. `pip install -e .[dev,eval]` adds pytest and the parquet
readers for `lobes eval`; `.[ocr]` adds the second image reader (RapidOCR, cpu).

Effort is one knob for everything that costs time: whether the reasoning model thinks
and how long, how many witnesses an item may draw (the fixed ones above, then hot
samples of the reasoning lobe), how many program repairs, and the hard caps per item.
`effort:` in lobes.yaml is the default (medium), `--effort` overrides it per call, and
the api reads OpenAI's `reasoning_effort` field. `auto` starts at medium and climbs to
high, then xhigh, each time the witnesses run out without a majority, before the
escalate model joins as one more witness. The table is `EFFORT` in lobes/runner.py.

| level  | thinking | think tokens | witnesses | repairs | cap: calls | cap: tokens | cap: seconds |
|--------|----------|--------------|-----------|---------|------------|-------------|--------------|
| low    | off      | 0            | 3         | 1       | 8          | 6000        | 120          |
| medium | on       | 6000         | 3         | 2       | 16         | 16000       | 300          |
| high   | on       | 16000        | 5         | 3       | 24         | 40000       | 600          |
| xhigh  | on       | 32000        | 8         | 4       | 36         | 80000       | 1200         |
| max    | on       | ctx          | 12        | 6       | 60         | none        | none         |

A cap ends the item with what the reasoning lobe produced, hedged. low also turns the
ladder off. xhigh and max need `ctx` above their thinking cap.

## Results

Filled in after the run. See [eval/REPORT.md](eval/REPORT.md).

## Status

Works on my machine (RTX 4070 Laptop, 8 GB). Verified: the eight models load and
answer under grammar constraints, the swap chain stays inside the budget, the
retry/vote/escalate ladder fires, the api round-trips, screenshots reach the
perception lobe. Built but not verified: the remote rung and remote provider (no key).
Not done: multi-turn memory, anything concurrent.

## Layout

    lobes.yaml        models, providers, profiles
    lobes/            runtime, one file per concern, lobes in lobes/lobe/
    eval/             prereg, suites, results, report
    docs/             design notes, decisions
    tests/            pytest, no GPU needed
    models/ bin/      downloaded, gitignored
    runs/             one directory per task with the full trace

MIT.
