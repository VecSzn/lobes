# Lobes 设计

中文 | [English](ARCHITECTURE.en.md)

这篇写现在的运行时：五个槽各干什么、一次请求怎么走、上限怎么算、代码怎么组织。成绩在 [`../eval/REPORT.md`](../eval/REPORT.md) 和 README。

一条链上串几个小模型，错误会叠加。拆开只在两种情况下划算：某个模块拿到的是别人没有的信息（图片、工具结果），或者干的活性质不同（做出来 vs 读一遍）。同一档小模型换个提示词再串一遍，这两条都不满足。

## 机器与后端

主要目标是我的 8 GB 的 RTX 4070 Laptop，`lobes.yaml` 给模型留 6800 MB（`llama.vram_budget_mb`），是 8188 减掉桌面已经占的部分。

推理走 llama.cpp b10951 的 llama-server，router 模式：启动时不指定模型，`--models-preset` 指一个 ini，`POST /models/load`、`/models/unload` 动态装卸，`GET /models` 报状态。ini 由 `install.write_presets` 在每次 `lobes serve` 时重写，只写权重文件已经在本地的模型。`[*]` 段写 `c`（上下文）、`jinja = true`、`n-gpu-layers = 999`、`fit = off`，`threads` 和 `parallel` 配了才写。Windows 取预编译包，Linux 从打了 tag 的源码编，需要 git、cmake、nvcc。

评测租的 5090 上，`eval/pod.sh` 先改工作副本：预算调到 28000、删掉 `device: cpu` 让分类器也上 GPU、给常驻模型一个真实的 `vram_mb`、加上 `threads`，再单独把 qwen3.5-4b 的上下文提到 65536。四道题一起跑靠的是 `lobes eval --workers 4`，不然大半时间花在换模型上。

## 槽和填法

`config.lobe(cfg, 槽名, profile)` 查 lobes.yaml，返回 `("local", "qwen3.5-4b")` 这样的模型，或者 `("impl", "passthrough")` 这种不是模型的填法。运行时只认槽名，模型名一个都不写死。

默认 profile `specialists`：

| 槽 | 干什么 | 填的 | 放哪 |
|---|---|---|---|
| executive | 判这个请求难不难 | brick-2-max Q8 | CPU 常驻 |
| perception | 读图：描述一遍，把字抄出来 | qwen3.5-2b + mmproj | GPU 换入 |
| reasoning | 把请求做出来、写回复；`python` 自己调 | qwen3.5-4b | GPU 换入 |
| motor | 工具手：按 reasoning 说的去调工具，再从结果里作答 | granite-h-micro | GPU 换入 |
| language | 拿写好的草稿对着请求读一遍，说哪里不对 | gemma4-e2b | GPU 换入 |

`v1` profile 另外填 `router` 槽和 chat / code / math / documents / knowledge 几个专家槽：router 挑一个，`Ctx.slot` 就把 reasoning 指向那个槽。`check` 不是独立的槽，它是同一个 reasoning 模型读自己的草稿。

评测用的 profile 里 `none` 表示槽空着，`passthrough` 表示这一步不过模型。

## 一次请求怎么走

```
用户
 │
 ├─ executive.intake：easy / 其它 → simple / hard；有 router 时再挑一个专家
 ├─ 带图：perception 描述 + ocr 抄字，各成一条带来源的观察
 ├─ relay 把 language 放在草稿前时：language.requirements 写下回复要满足什么
 ├─ reasoning.solve：思考、调工具、写草稿
 │    ├─ python 它自己调
 │    └─ 文件 / shell / 网页 / 截屏 → motor 去调，再用话把结果交回来
 ├─ hard 且配了检查：language.review 读草稿；打回就重写一遍
 └─ 撞上限且草稿还没有：reasoning.answer_now 拿已有对话再答一次
```

### executive

难度模型是个分类器，提示词只问一句：easy、medium 还是 hard，温度 0，最多 5 个 token。只有回答以 easy 开头才走 simple，其余一律 hard。请求很长时按头 1200 尾 400 截一段送过去（`ends()`），整篇文档在 CPU 上要跑好几秒，而要求通常在两头。

带图片的请求跳过分类，一律算 hard。profile 里没有 executive 模型时也一律 hard。

profile 填了专家槽、又填了 `router`、并且请求不带图时，router 用受 schema 约束的 enum 挑一个话题，解析不出来就返回 None，reasoning 接着答。

客户端自己跑工具的那种请求，工具结果回来时算同一个请求的后续步骤：`_seen` 按 (profile, 工具调用 id) 记下 route、topic、时间戳、已有的打回和已经跑过几道检查，最多留 1024 条，满了从最老的丢。客户端把原始请求压缩掉了也还认得出来。

### perception

视觉模型按 schema 交三样：description、text（图里所有可读文字，逐字）、details。结果写进 run 目录的 `perception_N.json`，摘要作为一条 `lobe:perception` 观察进 state。

装了 `lobes[ocr]` 的话 RapidOCR 再单独读一遍，只留置信度 0.5 以上的行，作为另一条 `tool:ocr` 观察。两条分开放，不合并。没装就跳过。

工具返回图片时（比如 screenshot）也走这里。

### reasoning

它拿到的材料（`brief()`）是：请求原文；有图就加上那几条观察，每条标着来源；relay 把 language 放在前面时加上它写的那份要求，并注明这是另一个 lobe 对请求的读法，请求原文才算数；再加一行本地时间（问日期时 qwen 会自己编一个，每轮只盖一次戳，工具步骤才能命中缓存）。

工具给法分三种：

- api 客户端自带工具：就用客户端那份，调用发回客户端跑，请求到此结束。
- profile 里有 motor：reasoning 只拿 `python`，另加一个 `motor` 工具，用话描述要什么。
- 没有 motor：reasoning 拿全部工具。

模型在 lobes.yaml 里写了 `tools: false` 的，一个工具都不给，它的调用会以纯文本回来，没人跑。

循环里有几处是量出来的：

- 回复空、只有思考：把思考当 assistant 轮补回去再问一次，不带思考预算。不这么做 qwen3.5-4b 会把同一个失败调用重发 30 次。再答还是空，就把那段思考当答案。
- 工具调用的参数写到一半撞上限：什么都不跑，客户端也拿不到这个残缺调用，重试一次给两倍的回复空间并强制思考。第二次还写不完就停下来说清楚。
- 正文写到一半撞上限：单独再要一次结论，不思考，最多 2048 token，接在草稿后面。结论自己也被截断就丢掉，因为读的人会把最后一段当答案（GPQA 09-18 那 23 道：接上被截结论的对 1 道、19 道连答案都没有，草稿单独交是 7 道和 13 道）。
- 同一个 (工具, 参数, 结果) 出现三次就停下，把结果交出去，不再发第四次。出现两次把思考预算抬到本档的上限并记一条 `stalled`。这条规则对客户端跑的工具同样生效，跨整个请求算。

撞上限时草稿还没写出来的，`answer_now` 再给一次机会：一次调用、不给工具、最多 2048 token，跳过上限检查。悬空的工具调用先补一条「没跑，预算用完了」，不然服务器不收这段对话。它读过的文件和想出来的东西因此还能交到读的人手上。

### motor

工具手拿到的是一句话，不是调用。它先调一轮工具，然后第二次调用不带工具，从结果里作答。要再走一步就得回到 reasoning，因为计划在那边。

结果留在 motor 自己的对话里。以前把原始结果直接交回去，等于把接力的上下文变成求解叶的上下文：`cat` 一个文件就把整个文件搬进求解叶的对话，之后每一轮都重新预填一遍。第二个模型值钱的地方，正是它替第一个留住了这些。

第二次调用什么都没说时，把原始结果交回去。

### language

放在草稿后面（`relay.language = "review"`，默认）时，它拿到请求、reasoning 跑过的每个工具连真实结果、以及草稿，回一个 OK 或者一句哪里不对。判定只看第一行，去掉空白和 `.!*\`` 再比 OK；用纯文本不用工具调用，是因为 gemma-4-E2B 写 send_back 时多半不带调用标记，回来就是文本，直接算通过了。

放在草稿前面（`"requirements"`）时，它只看请求，按 schema 写 1 到 16 行、每行不超过 300 字的要求清单，之后不再审查。schema 是必须的：光用话叮嘱它别回答请求，前 3 道里有 2 道它直接把答案写了，而那段会作为「回复要满足什么」进到求解叶的材料里。这个位置是从另一半量出来的：100 道 ifeval 里判错的答案，条条都是只差一个条件没满足，事后读草稿只抓出 25 道里的 2 道。列条件是抄写，核条件是计数，2B 会前者不会后者。

打回后重写一遍，再走下一道检查。最后一道检查打回时不重写，只把意见记成 uncertainty：三轮 250 道跑下来，审查打回 26 个答案，重写一个没救回、还弄坏两个，而且意见基本都在跟算术较劲，那恰好是 gemma 比求解叶读得更差的部分。

`checks_on` 默认 `hard`，simple 路线不走检查。同一个检查者通过过当前草稿就不重复读（`recheck: False`）。已经跑过几道检查会跨客户端的工具步骤记住。

## 上限

| 档 | 每次调用思考 token | 每个请求生成 token | 模型调用 | 秒 |
|---|---:|---:|---:|---:|
| low | 1,024 | 16,384 | 12 | 180 |
| medium | 4,096 | 32,768 | 20 | 420 |
| high | 8,192 | 65,536 | 30 | 900 |

思考之外，一次回复另给 4,096 token（`ANSWER`），补结论给 2,048（`CONCLUSION`），simple 路线的思考固定 1,024（`SIMPLE_THINK`）。

三档走的是同一条接力，档位只决定 reasoning 每次调用能想多久和这几个上限，不改变谁跟谁交接。没有自动升档。每次模型调用前查一遍上限，本次的 `max_tokens` 截到这个请求剩下的生成 token，思考预算再截到 `max_tokens` 的一半。工具不算调用，最后一次允许的调用写出来的程序照样跑。秒数既挡新调用也限每次等 llama-server 的时间，但不打断模型加载和工具执行，所以一个请求可能超过这个秒数。撞上限就把已有的草稿交出去，附一句说明。

`auto`、`xhigh`、`max` 分别当 medium、high、high 收下。入口是 lobes.yaml 的 `effort:`、`lobes ask --effort`、api 的 `reasoning_effort`。

relay 本身也可以按 profile 覆盖（lobes.yaml 的 `relays: {profile: {...}}`）：`think` 有 effort / off / escalate / first 四种，某个模型在 lobes.yaml 里写了自己的 `think` 就以它为准；`checks_on` 是 hard 或 all；`language` 是 review 或 requirements；`checks` 按档列出要走的检查；`recheck` 决定通过过的检查者要不要再读一遍。

## 显存

模型管理器在 llama-server 的 router 上加一层按兆算的预算。router 只管模型个数（`--models-max`），这里管的是兆：装 X 之前，按最久没用的顺序卸掉非常驻模型，直到 X 装得下。

`resident: true` 的永不卸载，`vram_mb` 为 0 的不进候选。最近使用按一个自增计数器排，不用 `time.time()`，那个在 Windows 上每 15 ms 才跳一次。装卸都计时，装完读一次 nvidia-smi 存在事件里，好拿来校准 yaml 里的估值。轮询间隔 0.05 秒，超时 300 秒，加载失败连 exit code 一起报出来。并行跑的任务共用一个 router，所以整个装卸加了全局锁，不然对同一个正在加载的模型再发一次 load 会得到 400。

`specialists` 的换入顺序就是请求的顺序：reasoning、motor、language。下一个装不下时按最久没用的卸，卸到装得下为止；一个都卸不动就报错，不会悄悄超预算。

## 工具

八个，都是普通函数（`lobes/tools.py`）：`python`、`shell`、`read_file`、`write_file`、`edit_file`、`web_search`、`web_fetch`、`screenshot`。注册表导出 openai 格式的 schema 交给 llama-server 的 chat 模板。

`python` 是每个请求一个长期进程，像 notebook 一样：上一次定义的函数、导入的模块下一次还在（09-17 那轮 2469 次调用里 76 次 NameError，其中 39 次是之前导入过的模块）。最后一个表达式的值会像 REPL 那样回显。超时后解释器重启。

没有沙箱。`python` 和 `shell` 执行模型写的任何东西，只有 10 秒超时拦着。以 root 启动时子进程降到 `nobody`（`UNPRIVILEGED`）；以你自己的身份启动时就是你的权限。文件工具过 `_inside()` 锁在本次 run 的工作目录里，这两个锁不住。不信任的负载请整个放进容器。

## 代码怎么组织

两个进程：llama-server router（`lobes serve` 起，每个模型它自己开子进程），和一个 Python 进程。

CLI：`install`、`serve`、`models`、`load`、`unload`、`ask`、`eval`、`api`，另有 `providers test`。`lobes api` 在 8090 开两个端点：`/v1/chat/completions`（`lobes-v1` 走 v1 profile，`lobes/<名字>` 走任意 profile）和 `/v1/responses`（Codex 从 chat completions 换过去之后说的那套）。请求自带 `tools` 时，工具调用交回客户端跑；不带就用本地的。`stream=true` 时接力的每一步作为 `reasoning_content` 发出去，答案作为 `content`。

provider 只有一个接口：`providers.chat(provider, model, messages, *, schema, images, thinking, thinking_budget, tools, temperature, max_tokens, seed, timeout, ctx, on_delta) -> Reply`，一个 OpenAI 兼容适配器同时覆盖 llama-server 和 LM Studio。思考开关每次显式发 `chat_template_kwargs.enable_thinking`。种子每次调用加上调用序号。

runner.py 一条直线，状态是一个 `TaskState`。每步追加写 `runs/<task_id>/trace.jsonl`，记录类型有 start、model、intake、requirements、call、tool、cut、stalled、review、cap、stop、language_error、final。工具的原始结果各存一个 json。没有消息总线，没有重试回路。

模块之间只传一样结构化的东西：`schema.py` 的 `Observation`（source、ref、summary），感知写、材料读。其余都是纯文本和 `TaskState` 的字段。

```
Lobes/
  README.md  README.zh-CN.md  lobes.yaml  pyproject.toml
  docs/    ARCHITECTURE.md  ARCHITECTURE.en.md  img/
  lobes/   cli.py config.py providers.py schema.py models.py runner.py tools.py install.py
           api.py responses.py eval.py
           lobe/  executive.py perception.py reasoning.py motor.py language.py
           hard/  官方判分器：ifeval、math500、bfcl、livecodebench、repo
  eval/    suites/（题库 jsonl，make.py 出 tools 和 multistep 的题）  data/（题库和 repo 快照）
           PREREG*.md  REPORT.md  plot.py（README 的图）  pod.sh（租的 5090 怎么跑）
  tests/   test_lobes.py  test_provider_limits.py  test_responses.py
  runs/  models/  eval/results/      不进 git
```

栈：Python 3.11+，httpx、pydantic v2、typer、rich、pyyaml、pillow、starlette、uvicorn；评测另加 pandas 和 pyarrow 读 parquet；OCR 可选 rapidocr-onnxruntime（`lobes[ocr]`）。不用 LangChain 或 LangGraph：控制流正是这个项目要测的东西，藏起来就没法测了。

## 评测

`lobes eval` 跑题库，一道题一行 JSONL，可以断点续跑。条件是 lobes.yaml 里的 profile，也可以用 `lobes/eval.py` 的 `CONDITIONS` 里预注册的代号。结果落在 `eval/results/<tag>/<条件>-s<种子>.jsonl`，tag 把不同代码版本和不同机器的结果分开。

判分全是代码，没有模型裁判：GSM8K 比最后一个数，HumanEval 和 MBPP+ 跑官方测试，ifeval、math500、bfcl、livecodebench 用各自上游的判分器（`lobes/hard/`，官方代码原样 vendored）。每道题记对错、弃答、token、秒、换模次数、显存峰值、调用数、走的哪条路线、撞没撞上限。

假设和阈值在跑之前写死在 `eval/PREREG*.md`，文件冻结，偏离记在 REPORT。那几轮跑的是更早的运行时，数字按当时测到的样子留着。

## 别人做过的

最像的是 HuggingGPT（2023，LLM 当控制器调度专家模型）和 Mixture-of-Agents（2024）。NVIDIA 2025 那篇「Small language models are the future of agentic AI」讲的是同一个方向。模型级的级联和路由有 FrugalGPT、RouteLLM，speculative decoding 也是小大配对。工具这块是 ReAct、Toolformer、PAL、Gorilla 和 BFCL。认知架构有 Minsky 的 Society of Mind、ACT-R、SOAR、Global Workspace、CoALA。MoE（Switch、Mixtral）是 token 级、网络内、联合训练出来的路由，跟这里的模块级路由不在一个层面。BAIR 2024 管这类东西叫 compound AI systems。

脑区这个比喻有个漏洞：脑区是一起训练出来的，有共享表征；这几个模型不是，中间传的是有损的文本。真正对得上的只有感知（ViT）、语言和推理（LLM）、运动（工具执行）、执行控制（分类器）。

## 没做的

多轮记忆、真沙箱、预测性预加载、拿自己攒的 trace 微调小模型。并发只在所有模型都常驻时成立（5090 上评测就是四道题一起跑）。
