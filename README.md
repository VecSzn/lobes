# Lobes

An experimental modular AI runtime that coordinates specialized small-model lobes with evidence-based verification and dynamic model swapping, built to fit in 8 GB of VRAM.

The idea: instead of one general model, split the job across a handful of small
models from different families, each doing one thing (planning, seeing, reasoning,
calling tools, writing, checking), and swap them in and out of the GPU as needed.
Whether that actually buys anything over a single 9B model at the same budget is
the question this repo exists to measure. Design notes are in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) (Chinese).

Status: early. The runtime, model manager and installer work on my machine
(RTX 4070 Laptop, 8 GB). Numbers in the docs are measured where they say so and
guesses where they don't. See [docs/DECISIONS.md](docs/DECISIONS.md) for what
changed along the way and why.

## Quick start

Windows, NVIDIA GPU, Python 3.11+. Everything else gets downloaded.

    pip install -e .
    copy .env.example .env          # only needed for remote providers
    lobes install                   # llama.cpp + ~18 GB of GGUFs for the default profile
    lobes serve                     # llama-server in router mode, keep this running
    lobes ask "what is 17 * 23"

`lobes models` shows what is loaded and how much VRAM it takes. `lobes ask --lobe reasoning --schema`
talks to one lobe directly and prints the raw envelope.

Which model fills which lobe is decided entirely by `lobes.yaml`. A lobe can point at a
local model, an OpenAI-compatible remote (`openai/gpt-...`, `deepseek/...`, LM Studio on
port 1234), or a plain code implementation. Two profiles ship: `specialists` (one family
per lobe, swapping is the normal case) and `shared` (one 4B model wearing different hats,
used as the control condition).

## Layout

    lobes.yaml        models, providers, profiles
    lobes/            runtime
    docs/             design notes, decisions
    eval/             benchmark harness and preregistration
    models/ bin/      downloaded, gitignored
    runs/             one directory per task with the full trace

## License

MIT.
