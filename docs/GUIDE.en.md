# Using Lobes

[中文](GUIDE.md) | English

The README has the three commands. This page goes through them one at a time, plus how I hook Lobes up to Codex and DeepSeek Harness. It's what I'd want the first time I set this up.

## What you need

An NVIDIA card and Python 3.11 or newer. 8 GB of VRAM is enough for the default `specialists` profile. With less than that you have to lower `llama.vram_budget_mb` in `lobes.yaml`, and the `vram_mb` of each model, yourself.

On Windows, Lobes downloads a prebuilt llama.cpp, so you don't need a compiler. On Linux it builds `llama-server` from source, so `git`, `cmake` and `nvcc` all have to be on `PATH`. If one is missing, the install stops at that step.

## Install

```bash
pip install -e .
lobes install
```

`lobes install` downloads llama.cpp and the models the current profile uses, then writes `models/models.ini`. The models add up to several GB, so the first run takes a while. If the download breaks halfway, just run it again. Files that already finished don't get downloaded twice.

To download one profile's models only:

```bash
lobes install --profile specialists
```

If you already put llama.cpp in place yourself, add `--skip-llama`.

There are two optional extras:

```bash
pip install -e ".[ocr]"        # RapidOCR, a second reader for text in images
pip install -e ".[dev,eval]"   # what the tests and the benchmark need
```

## Start the server

```bash
lobes serve
```

This keeps the terminal busy, so leave it open. It starts llama.cpp's router on port 8080. The router starts with no model loaded and loads or unloads them when a request needs them. `lobes serve` rewrites `models/models.ini` every time it starts, so after you change the models in `lobes.yaml`, restart it.

## Ask something

Open a second terminal:

```bash
lobes ask "write me a snake game in python"
```

The first request is slow because the model has to be loaded into VRAM first. After that, while the model is still loaded, the next request starts answering right away.

With an image:

```bash
lobes ask --image shot.png "what is on this screen"
```

If you want it to think longer:

```bash
lobes ask --effort high "sort this CSV by the third column and average each group"
```

`--effort` can be low, medium or high, and medium is the default. The level changes how long reasoning may think on each call and the limits for the whole request. The models and the order they run in stay the same.

To see which models are in VRAM right now:

```bash
lobes models
```

## Connect Codex

Start the API first:

```bash
lobes api
```

It opens three endpoints on port 8090: `/v1/chat/completions`, `/v1/responses` and `/v1/models`. Codex uses `/v1/responses`.

Then put this in `~/.codex/config.toml`:

```toml
model = "lobes/specialists"
model_provider = "lobes"
model_reasoning_effort = "medium"

[model_providers.lobes]
name = "Lobes"
base_url = "http://127.0.0.1:8090/v1"
wire_api = "responses"
```

`base_url` should end at `/v1`. Codex adds `/responses` by itself, and if you write it out you get a 404.

`model` is a profile, written as `lobes/<profile>`. Right now `lobes.yaml` has `specialists` (the default), `shared`, `v1`, `bare-9b`, `bare-4b`, `single-9b` and `single-4b`. `lobes-v1` also works and means `v1`.

`model_reasoning_effort` maps to the levels above. `auto` counts as medium, and `xhigh` and `max` count as high. Codex sometimes sends `minimal`, which Lobes doesn't know, so it falls back to the default in `lobes.yaml`.

There's no `env_key` because the API has no authentication and never checks a key. If your client insists on one, any string works.

When Codex brings its own tools, the tool calls go back to Codex and Codex runs them.

## Connect DeepSeek Harness

The harness uses chat completions, so it talks to the same port 8090.

Add this to `$DSH_HOME/settings.yaml`:

```yaml
llm-pi-ai:
  providers:
    lobes:
      api: openai-completions
      baseURL: http://127.0.0.1:8090/v1
      models:
        - id: lobes/specialists
```

After that the provider shows up in the model picker. Pick it once and it becomes the default for new sessions.

Every turn, the harness sends a snapshot of its workspace and policy as a user message. Lobes recognises it (the `CONTEXT` constant in `api.py`) and moves the latest one into the system prompt, so it doesn't get answered like a question.

## If something goes wrong

If `lobes ask` can't connect to port 8080, `lobes serve` probably isn't running.

If VRAM runs out, the model manager raises an error instead of going over the budget. Lower `vram_budget_mb`, or set `resident: true` on a model so it never gets unloaded.

Every request writes its whole run to `runs/<task_id>/trace.jsonl`, one line per step, and each tool's raw output is saved as its own json file. When an answer comes out wrong, that file is the first thing to read.

Remember that the python and shell tools are not sandboxed. The warning in the README explains what that means.
