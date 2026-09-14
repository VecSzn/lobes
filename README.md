# Lobes

Six small models, one job each, swapped in and out of an 8 GB GPU. A runner
decides who goes next and a verifier that never sees the candidate answer decides
whether it is done.

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

    executive    picks the route (rules first, a 1.2B model for the rest), writes the plan
    perception   describes images and screenshots; an ocr engine reads them a second time
    reasoning    produces claims + answer, or asks for one python run first; samples and
                 picks when the task carries its own examples or nothing can check it
    motor        turns a plan step into one tool call
    verifier     evidence check in code, then the task's own >>> examples, a test written
                 without seeing the code, a blind re-solve, or a second look at the image
    language     final wording; a code check stops it from changing numbers, and an
                 answer nothing backs gets a "not sure" in front

Which model fills which lobe is only in `lobes.yaml`. A lobe can point at a local
GGUF (llama-server in router mode), an OpenAI-compatible remote, or plain code.
Profiles: `specialists` (one family per lobe), `shared` (one 4B for everything),
plus the single-model controls used by the eval.

When the verifier says no, reasoning retries with thinking on, then votes over three
samples, then hands the task to the 9B, then to a remote API if a key is set.

Tools: python, shell (timeout, blacklist), read/write/edit file, web fetch,
screenshot. Every tool output is a file in `runs/<task>/` and every claim that says
"tool" has to quote a number that is actually in that file.

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
and how long, how many samples a vote draws, how many retries and steps a task gets,
whether the escalation ladder is on, and from high up two more things. The reasoning
lobe rereads its own draft once for a concrete mistake before the verifier sees it
(reflect), and the verifier scores several candidates per step and keeps the best
(width). `effort:` in lobes.yaml is the default (medium), `--effort` overrides it per
call, and the api reads OpenAI's `reasoning_effort` field. `auto` starts at medium and
climbs to high, then xhigh, each time the retries run out, before a bigger model takes
over. The table is `EFFORT` in lobes/runner.py.

| level  | thinking        | think tokens | samples | retries | steps | reflect | width |
|--------|-----------------|--------------|---------|---------|-------|---------|-------|
| low    | never           | 0            | 1       | 1       | 6     | no      | 1     |
| medium | on the retry    | 6000         | 3       | 2       | 10    | no      | 1     |
| high   | always          | 16000        | 5       | 3       | 14    | yes     | 2     |
| xhigh  | always          | 32000        | 8       | 4       | 20    | yes     | 3     |
| max    | always          | ctx          | 12      | 6       | 30    | yes     | 4     |

low also turns the ladder off. xhigh and max need `ctx` above their thinking cap.
Reflection leaves alone a draft a tool printed, one that passed its examples, or one
every sample agreed on; a changed answer is written to the trace and to the final
uncertainties. Vision tasks skip the search, each candidate there already costs three
readers.

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
