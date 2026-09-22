# Lobes architecture

[中文](ARCHITECTURE.md) | English

How the current runtime works, from the five slots down to the code layout. Scores are in the README.

Chaining several small models compounds their errors. Splitting the work pays off in two cases: a module receives information the others do not have (an image, a tool result), or it does a different kind of work (writing an answer versus reading one). Running the same size of model again with a different prompt satisfies neither.

## Machine and backend

The main target is an RTX 4070 Laptop with 8 GB. `lobes.yaml` reserves 6800 MB for models (`llama.vram_budget_mb`), which is 8188 minus what the desktop already holds.

Inference runs through llama-server from llama.cpp b10951 in router mode: it starts without a model, `--models-preset` points at an ini file, `POST /models/load` and `/models/unload` load and unload on demand, and `GET /models` reports status. `install.write_presets` rewrites that ini on every `lobes serve`, listing only models whose weight files are present. The `[*]` block sets `c` (context), `jinja = true`, `n-gpu-layers = 999` and `fit = off`; `threads` and `parallel` are written only when configured. Windows takes a prebuilt release, Linux builds from the tagged source and needs git, cmake and nvcc.

On the rented 5090, `eval/pod.sh` edits the working copy first: the budget goes to 28000, `device: cpu` is deleted so the classifier runs on the GPU too, resident models get a real `vram_mb`, `threads` is added, and qwen3.5-4b gets a context of 65536. The four items that run at once come from `lobes eval --workers 4`, without which most of the wall time goes into swapping models.

## Slots and how they are filled

`config.lobe(cfg, slot, profile)` reads lobes.yaml and returns either a model, as in `("local", "qwen3.5-4b")`, or something that is not a model, as in `("impl", "passthrough")`. The runtime only knows slot names and has no model name written into it.

The default profile is `specialists`:

| slot | job | filled with | where |
|---|---|---|---|
| executive | decide whether the request is hard | brick-2-max Q8 | CPU, resident |
| perception | read images: describe them, copy out their text | qwen3.5-2b + mmproj | GPU, swapped |
| reasoning | work the request out and write the reply; calls `python` itself | qwen3.5-4b | GPU, swapped |
| motor | the tool hand: makes the calls reasoning asks for, then answers from the results | granite-h-micro | GPU, swapped |
| language | read the finished draft against the request and say what is wrong | gemma4-e2b | GPU, swapped |

The `v1` profile also fills a `router` slot and the code / math / knowledge expert slots. The router only chooses among the slots a profile has filled, and `Ctx.slot` points reasoning at the one it picks. `check` is not a slot of its own, it is the same reasoning model reading its own draft.

In the evaluation profiles, `none` leaves a slot empty and `passthrough` means that step runs no model.

## How a request moves

```
user
 │
 ├─ executive.intake: easy / anything else -> simple / hard; with a router, pick an expert
 ├─ images: perception describes, ocr transcribes, each a separate labelled observation
 ├─ relay puts language before the draft: language.requirements writes down what the reply must satisfy
 ├─ reasoning.solve: think, call tools, write the draft
 │    ├─ python it calls itself
 │    └─ files / shell / web / screen -> motor makes those calls and reports back in words
 ├─ hard route with checks configured: language.review reads the draft; a rejection triggers a rewrite
 └─ a cap hit with no draft yet: reasoning.answer_now answers from the conversation it already has
```

### executive

The difficulty model is a classifier, and the prompt asks it one thing: easy, medium or hard, at temperature 0 and 5 tokens. Only a reply that starts with easy takes the simple route; everything else is hard. A long request is cut to its first 1200 and last 400 characters (`ends()`), because a whole document takes seconds on the CPU and the ask is usually at the ends.

A request with images skips classification and is always hard. So is every request when the profile has no executive model.

When the profile fills expert slots, fills `router`, and the request carries no images, the router picks a topic through a schema-constrained enum. A reply that does not parse returns None and reasoning answers instead.

For an api client that runs its own tools, the results coming back count as later steps of the same request: `_seen` records the route, topic, timestamp, the rejections so far and how many checks have run, keyed by (profile, tool call id). It keeps 1024 entries and drops the oldest first, so the request is still recognised after the client has compacted it away.

### perception

The vision model returns three fields against a schema: description, text (every piece of readable text, verbatim) and details. The result is written to `perception_N.json` in the run directory and its summary becomes a `lobe:perception` observation.

With `lobes[ocr]` installed, RapidOCR reads the image separately, keeping lines scored 0.5 and above, and that becomes a second observation labelled `tool:ocr`, kept apart from the first. Without the package the step is skipped.

A tool that returns an image, such as screenshot, comes through here too.

### reasoning

Its brief holds the request; the observations when there are images, each labelled with its source; the requirements list when the relay puts language first, named as one lobe's reading of the request so that the request itself is what counts; and one line of local time, because without it the model makes up a date. The time is stamped once per turn so the tool steps stay cached.

Tools are handed over in one of three ways:

- the api client brings its own: those are the specs, and a call ends the request by going back to the client;
- the profile has a motor: reasoning gets `python` and a `motor` tool it describes its needs to in words;
- no motor: reasoning holds every tool.

A model with `tools: false` in lobes.yaml gets none at all, and its calls come back as plain text that nothing runs.

A few rules in the loop were added after watching runs go wrong:

- An empty reply with only thinking: the thought goes back as an assistant turn and the model is asked again without a thinking budget. Before this, qwen3.5-4b once sent the same failing call 30 times. If it still says nothing, the thought becomes the answer.
- A tool call cut off while its arguments were being written: nothing runs, the client never sees the broken call, and the retry gets twice the room and is forced to think. Cut a second time, the request stops and says so.
- The body cut off mid-sentence: the conclusion is asked for on its own, without thinking, in at most 2048 tokens, and appended after the draft. A conclusion that is itself cut is dropped. Readers take the last block as the answer, and on GPQA a cut conclusion left more items with no answer at all than the draft did on its own.
- The same (tool, args, result) three times ends the request with that result instead of a fourth call. Twice raises the thinking budget to the level's cap and writes a `stalled` record. The rule covers the client's tool calls as well, across the whole request.

When a cap lands before there is a draft, `answer_now` gets one more turn to answer from what the model already read and worked out: one call, no tools, at most 2048 tokens, with the caps that ended the request not applied to it. Any tool call left dangling is answered with "Not run: the request ran out of budget." first, because no server takes a conversation with an unanswered call in it.

### motor

Reasoning tells the tool hand in a sentence what it needs. Motor makes one round of tool calls, and its second call has no tools, so it answers from what came back. A request that needs another step returns through reasoning, which is the lobe holding the plan.

The results stay in motor's own conversation. When they were handed back raw, a `cat` of a file put the whole file into the solver's conversation, and every later turn prefilled it again.

When the second call says nothing about the results, the raw results go back instead.

### language

After the draft (`relay.language = "review"`, the default) it receives the request, every tool reasoning ran with its real result, and the draft, and replies either OK or with one statement of what is wrong. Only the first line is read, stripped of whitespace and of `` .!*` `` before it is compared to OK. The verdict is plain text. When it was a tool call, gemma-4-E2B left the call marker off most of its send_back calls, so they arrived as text and passed.

Before the draft (`"requirements"`) it sees the request alone and writes a list of 1 to 16 lines, each at most 300 characters, against a schema. Without the schema, told in words not to answer the request, it answered 2 of the first 3 items anyway, and that answer would have gone into the solver's brief as something the reply has to satisfy. It runs before the draft because of how answers failed on ifeval: of 100 items, every wrong answer met all of the request's conditions but one, and reviewing the draft afterwards caught 2 of those 25. A 2B can copy the conditions out of a request but is bad at checking a draft against them.

A rejection is rewritten before the next check runs. A rejection from the last check is only recorded as an uncertainty. Over three 250-item runs the review sent back 26 answers, and rewriting them rescued none and broke two. Most of the criticism argued about the arithmetic, which gemma reads worse than the solver does.

`checks_on` defaults to `hard`, so the simple route runs no checks. A checker that already passed the current draft does not read it again (`recheck: False`). How many checks have run is remembered across a client's tool steps.

## Caps

| level | thinking tokens per call | generated tokens per request | model calls | seconds |
|---|---:|---:|---:|---:|
| low | 1,024 | 16,384 | 12 | 180 |
| medium | 4,096 | 32,768 | 20 | 420 |
| high | 8,192 | 65,536 | 30 | 900 |

On top of the thinking, a reply gets 4,096 tokens (`ANSWER`), a conclusion 2,048 (`CONCLUSION`), and the simple route thinks a fixed 1,024 (`SIMPLE_THINK`).

Every level runs the same relay with the same hand-offs. A level only sets how long reasoning may think per call and these caps, and it stays whatever the request asked for until the request ends. The caps are checked before every model call, each call's `max_tokens` is cut to what is left of the request's generated-token budget, and the thinking budget is then cut to half of that `max_tokens`. Tool runs do not count as calls, so a program written by the last allowed call still runs. The seconds cap stops new calls and bounds each wait on llama-server, but does not interrupt a model load or a tool, so a request can take longer than it. When a cap stops a request, the draft so far goes out with a note.

`auto`, `xhigh` and `max` are accepted as medium, high and high. The entry points are `effort:` in lobes.yaml, `lobes ask --effort`, and `reasoning_effort` in the api.

The relay itself can be overridden per profile through `relays: {profile: {...}}` in lobes.yaml: `think` is effort, off, escalate or first, and a model with its own `think` in lobes.yaml wins; `checks_on` is hard or all; `language` is review or requirements; `checks` lists the checks per level; `recheck` decides whether a checker that passed reads the draft again.

## VRAM

The model manager adds a budget in megabytes on top of llama-server's router. The router counts models (`--models-max`); this counts megabytes: before loading X it unloads the least recently used non-resident model until X fits.

A model with `resident: true` is never unloaded, and one with `vram_mb` at 0 is not a candidate. Recency is a counter rather than `time.time()`, which ticks every 15 ms on Windows. Loads and unloads are timed, and nvidia-smi is read once after a load and kept next to the estimate so the yaml numbers can be corrected. The poll interval is 0.05 s, the timeout 300 s, and a failed load is reported with its exit code. Tasks running in parallel share one router, so the whole load path takes a global lock: a second load of a model that is already loading returns a 400.

In `specialists` the swap order follows the request: reasoning, then motor, then language. When the next one does not fit, the least recently used model is unloaded until it does; when nothing can be unloaded the manager raises, so the budget always holds.

## Tools

Eight, all ordinary functions in `lobes/tools.py`: `python`, `shell`, `read_file`, `write_file`, `edit_file`, `web_search`, `web_fetch`, `screenshot`. The registry exports openai-format schemas for llama-server's chat template.

`python` is one long-lived process per request, like a notebook: a function defined or a module imported in one call is still there in the next. When each call started fresh, about half the NameErrors were a module the model had imported in an earlier call. The last expression is echoed the way a REPL would. The interpreter restarts after a timeout.

There is no sandbox. `python` and `shell` run whatever the model wrote, with a 10-second timeout as the only guardrail. Started as root, children drop to `nobody` (`UNPRIVILEGED`); started as yourself, they run with your permissions. The file tools go through `_inside()` and stay in the run's work directory; these two are not confined to it. Put the whole process in a container for untrusted workloads.

## How the code is organised

Two processes: the llama-server router (started by `lobes serve`, which spawns a child per model), and one Python process.

The CLI has `install`, `serve`, `models`, `load`, `unload`, `ask`, `eval`, `api`, plus `providers test`. `lobes api` opens two endpoints on port 8090: `/v1/chat/completions` (`lobes-v1` selects the v1 profile, `lobes/<name>` any profile) and `/v1/responses`, the Responses API that Codex speaks since it dropped chat completions. A request that carries `tools` gets the calls back to run itself; without them the lobes use their own. With `stream=true` the relay's steps go out as `reasoning_content` and the answer as `content`.

There is one provider interface: `providers.chat(provider, model, messages, *, schema, images, thinking, thinking_budget, tools, temperature, max_tokens, seed, timeout, ctx, on_delta) -> Reply`, and one OpenAI-compatible adapter covers both llama-server and LM Studio. The thinking switch is sent explicitly on every call as `chat_template_kwargs.enable_thinking`. The seed gets the call index added to it.

runner.py is a straight line and the state is one `TaskState`. Every step appends to `runs/<task_id>/trace.jsonl`, with records of kind start, model, intake, requirements, call, tool, cut, stalled, review, cap, stop, language_error and final. Each tool's raw result is stored as its own json.

Only one structured object passes between modules: `Observation` in `schema.py` (source, ref, summary), written by perception and read into the brief. Everything else is plain text and fields on `TaskState`.

```
Lobes/
  README.md  README.zh-CN.md  lobes.yaml  pyproject.toml
  docs/    ARCHITECTURE.md  ARCHITECTURE.en.md  img/
  lobes/   cli.py config.py providers.py schema.py models.py runner.py tools.py install.py
           api.py responses.py eval.py
           lobe/  executive.py perception.py reasoning.py motor.py language.py
           hard/  official graders: ifeval, math500, bfcl, livecodebench, repo
  eval/    suites/ (jsonl suites, make.py generates the tools and multistep answers)
           data/ (suite files and the repo snapshot)
           plot.py (the README figures)  pod.sh (how the rented 5090 runs)
  tests/   test_lobes.py  test_provider_limits.py  test_responses.py
  runs/  models/  eval/results/      not in git
```

Stack: Python 3.11+, httpx, pydantic v2, typer, rich, pyyaml, pillow, starlette, uvicorn; the evaluation adds pandas and pyarrow for parquet; OCR is optional through rapidocr-onnxruntime (`lobes[ocr]`). There is no LangChain or LangGraph, since the control flow is what the project measures and it should stay readable in runner.py.

## Evaluation

`lobes eval` runs the suites, one JSONL line per item, resumable. A condition is a profile from lobes.yaml, or one of the preregistered codes in `CONDITIONS` in `lobes/eval.py`. Results land in `eval/results/<tag>/<condition>-s<seed>.jsonl`, and the tag keeps one code version's run, or one machine's, apart from another's.

Judging is all code, with no model as a judge: GSM8K compares the last number, HumanEval and MBPP+ run the official tests, and ifeval, math500, bfcl and livecodebench use their upstream graders, vendored verbatim under `lobes/hard/`. Each item records correctness, abstention, tokens, seconds, swaps, peak VRAM, the number of calls, which route it took, and whether it hit a cap.

Each round's hypotheses and thresholds were written down before it ran, and every departure from them afterwards. The earlier rounds ran against earlier runtimes and their numbers are kept as they were measured.

## Prior work

The closest are HuggingGPT (2023, an LLM as controller dispatching to expert models) and Mixture-of-Agents (2024). NVIDIA's 2025 "Small language models are the future of agentic AI" argues the same direction. Model-level cascades and routing are FrugalGPT and RouteLLM, and speculative decoding pairs a small model with a large one too. On tools there are ReAct, Toolformer, PAL, Gorilla and BFCL. Cognitive architectures include Minsky's Society of Mind, ACT-R, SOAR, Global Workspace and CoALA. MoE (Switch, Mixtral) routes at the token level inside one jointly trained network, which is a different level from routing between modules. BAIR called this class of system compound AI systems in 2024.

The brain metaphor only goes so far. Brain regions are trained together and share representations; these models aren't, and they pass lossy text to each other. The parts that do map are perception (ViT), language and reasoning (LLM), motor (tool execution) and executive control (a classifier).

## Not done

Multi-turn memory, a real sandbox, predictive preloading, and finetuning a small model on the traces this thing collects. I think finetuning is the only way a small specialised module beats a small general model with a different prompt, and the project never got to it. Concurrency only works when every model stays resident (the 5090 evaluation runs four items at once); the 8 GB swapping setup serves one request at a time.
