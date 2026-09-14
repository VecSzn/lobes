# Lobes 设计笔记

2026-09-14，第一稿。想法是把几个小模型拼成一个「分区的脑子」，在一张 8G 的 4070 笔记本卡上跑。这篇先把原来的方案批一遍，再写我打算怎么做。

## 先说机器

4070 Laptop，8188 MiB。桌面开着的时候已经吃掉 1.5G 左右，所以模型实际能用的大概 6.5G，后面所有估算都按这个数。内存 32G，E 盘剩 168G。Python 3.12 有，torch 和 llama-cpp-python 没装，Ollama 没装。LM Studio 装了而且服务在 1234 端口跑着，里面有个 gemma-4-12b-qat（7.15G，这卡塞不满）和一个 nomic-embed。

推理后端定了用 llama.cpp 的 llama-server。今天最新是 b10951，Windows 有 CUDA 12.4 和 13.3 的预编译包。它现在有个 router 模式：不指定模型启动，用 `--models-preset` 写个 ini 描述每个模型，然后 `POST /models/load` 和 `/models/unload` 动态加载卸载，`--models-max` 限制同时加载的个数。`response_format` 支持 json_schema 做语法约束解码，多模态靠 mmproj 文件。这几样正好是我需要的全部。

## 原方案的问题

原来的想法是六个模型：协调器 2B、视觉 2B、工具调用 0.5–1.5B、推理 2–4B、输出 2B、验证 1–2B。

最大的问题是错误会叠加。每个 2B 模型单独看都不强，六个串起来，每一跳 90% 的正确率乘到最后只剩六成。模块化只有在模块拿到的是**不同的信息**（图片、工具结果）或者干的是**不同性质的活**（生成 vs 拿证据验证）时才有意义。如果只是同一档小模型换个提示词，那不叫分区，那叫同一个区被叫了六个名字。

具体到每个模块：

**协调器不该是 LLM。** 大部分路由决定看输入就知道：带图片走视觉，带代码块走编码，上一步是工具结果那下一步就是验证。这些用 Python 写 if 就行，零成本零错误。真正需要模型的只有「把一个大目标拆成几步」，而这事可以让主模型按 schema 输出一次，不需要单独养一个 2B 来干。每多一跳 LLM 就多一个错误源和一两秒延迟。

**视觉模型可以并进主模型。** 这是查完模型才发现的：Qwen3.5 是早期融合训练的视觉语言模型，4B 的视觉编码器就是一个 0.67G 的 mmproj 文件，挂上去就能看图。单独跑一个视觉模型反而更糟，它得先把看到的东西压成一段文字再交给下一个模型，中间丢的信息比省的显存值钱得多。

**工具调用模型可以删。** 工具调用难的不是写出合法 JSON，语法约束解码已经把这个问题彻底解决了，模型只能输出符合 schema 的东西。难的是决定该调哪个工具、传什么参数，这需要手里有完整的任务上下文，而上下文在主模型那儿。专门的小模型两头都不占。

**输出模型也删。** 推理模型想清楚了再让另一个模型改写成人话，只会丢东西：限定条件、不确定性都被抹平，改写的模型还会「修正」它没看懂的地方，这是新的幻觉来源。延迟翻倍，显存多一份。现在的 4B 写中英文都够用，thinking 模式本身就把思考和回答分开了。真要格式转换（JSON 转表格之类）用代码做。

**验证器是对的，但不能照原来那样做。** 详见下一节。

写到这里我一度想把六个模块砍成一个 4B 加一个 0.8B。后来觉得不对：砍的应该是**权重**，不是**模块**。合了模块，「脑区」这个东西就没了，剩下一个带验证循环的 4B，GitHub 上一抓一把。

所以最后是这样：六个脑叶（lobe）全部保留，每个是独立模块，有自己的输入输出契约、schema 和日志。**谁来干活是配置，不是代码。** 每个 lobe 可以填一个专用小模型，可以和别的 lobe 共用一份权重，也可以直接指向远端 API。几个 lobe 还各有一个非 LLM 的实现：执行中枢有 `rules`（状态机）和 `llm`（小模型规划器）两种，语言 lobe 有 `passthrough`（推理结果直接出）和 `llm`（改写）两种，验证 lobe 的证据检查本来就是代码。

配置分两套 profile：`specialists` 每个 lobe 各用一家的模型，加起来远超 6.5G，所以换入换出是真的在跑；`shared` 几个 lobe 共用 4B，常驻不换，快。默认 `specialists`。评测直接比这两套加单个 9B，「专用小模型到底值不值」就变成实验结果而不是我拍脑袋。

## 验证怎么做才不是摆设

用一个 1B 去审 4B 的答案，结果要么盖章要么加噪声。LLM 当裁判的附和偏差是有论文的，小模型更严重。我的做法是让验证尽量不依赖裁判的意见：

能执行的就执行。代码跑一遍，数值重新算一遍。回答里引用了工具结果的地方，直接在代码里做字面比对（数字、字符串、退出码）。这一层不用任何模型。

盲解。验证器拿到题目但看不到候选答案，自己做一遍，然后代码比对两个答案。看不到答案就没有锚定，附和这件事从根上就不存在。代价是多一次生成。

把回答拆成断言。主模型输出的时候按 schema 拆成一条条原子断言，每条标来源：来自工具、推导出来的、还是假设的。验证器只需要审「假设的」那几条，活变小变具体。

验证器不能单独给 PASS。PASS 必须有证据（执行通过、盲解一致、工具比对通过）。验证器只有否决权：RETRY、CONFLICT、VERIFY_WITH_TOOL。弱模型的否决很便宜，它的赞同一文不值，那就设计成赞同不重要。

最后是校准。评测的时候记下每类任务上验证器的精确率和召回率，哪类任务上它不比抛硬币强就关掉。这其实就是整个项目要验证的假设的一部分。

后面（V3）可以换个家族做裁判，gemma-4 或者 LFM2.5，跟 Qwen 的错误相关性低一些。

## 置信度、重试、升级

模型自报的 0.0–1.0 不能单独信。真正能用的信号：断言的证据类别、同一题采样几次的一致率、验证器判决、约束枚举题上的 token 概率（llama-server 能返回 logprobs）。这几个合成一个分数。

重试必须改变点什么：新证据、不同温度或种子、不同提示框架、换模型。原样重跑一次没有意义。本地最多重试两次。

升级阶梯：4B 不带 thinking → 4B 带 thinking → 4B 采样三次投票 → 换入本地 9B → 远端 API。每一级记账。

两个模型不一致：先找有没有工具能裁决，没有就升级，升级也不可用就把分歧原样告诉用户。不强行给答案。

## 模块之间传什么

一种信封，pydantic 定义，导出 JSON schema 给 llama-server 做约束。散文只允许出现在最终 answer 字段里。

```json
{
  "task_id": "t_20260914_001", "step": 3,
  "role": "solver", "model": "local/qwen3.5-4b",
  "kind": "step_result",
  "goal": "…",
  "observations": [{"source": "tool:python", "ref": "art_2", "summary": "stdout: 42"}],
  "claims": [
    {"id": "c1", "text": "结果是 42", "support": "tool", "evidence": "art_2"},
    {"id": "c2", "text": "对负数也成立", "support": "assumed", "evidence": null}
  ],
  "tool_calls": [{"name": "python", "args": {"code": "…"}}],
  "answer": null,
  "uncertainties": ["没测负数"],
  "confidence": {"score": 0.7, "basis": "consistency"},
  "next": {"action": "verify", "module": "verifier"},
  "budget": {"tokens_in": 1830, "tokens_out": 412, "ms": 2900}
}
```

kind 是 plan / step_result / tool_result / verdict / final 之一，next.action 是 tool / verify / answer / retry / escalate 之一。验证器的输出更短：

```json
{"verdict": "RETRY", "failed_claims": ["c2"],
 "proposed_check": {"tool": "python", "args": {"code": "assert f(-3) == …"}},
 "notes": "…"}
```

## 显存

Qwen3.5 的结构对这个项目特别友好：每四层只有一层是全注意力（4B 和 9B 是 32 层里 8 层，2B 和 0.8B 是 24 层里 6 层），其余是 Gated DeltaNet 线性层，状态固定大小。算下来 KV cache 每 token 4B/9B 约 32 KB，2B/0.8B 约 12 KB。去年的 Qwen3-4B 是 144 KB。这就是为什么现在能几个模型一起常驻。

Q4_K_M，f16 KV，16K 上下文：

| 模型 | 权重 | KV | 计算缓冲 | 合计 |
|---|---|---|---|---|
| Qwen3.5-0.8B | 0.56 | 0.19 | 0.15 | 0.9 |
| Qwen3.5-2B | 1.28 | 0.19 | 0.25 | 1.7 |
| Qwen3.5-4B | 2.74 | 0.51 | 0.35 | 3.6 |
| 4B + mmproj F16 | +0.67 | | +0.3（编码图片时） | 4.6 |
| Qwen3.5-9B IQ4_XS | 5.17 | 0.51 | 0.45 | 6.1 |
| gemma-4-E4B Q4_0 + mmproj | 4.59 + 0.56 | 没查 | | 5.8 左右 |
| gemma-4-12B qat | 6.98 | | | 塞不下，只能部分放 CPU |

上面是估的。实测（2026-09-14，b10951，ctx 16384，4 slots，nvidia-smi 增量，加载后各生成 512 token）：

| 模型 | 显存 MB | 热加载 ms | 卸载 ms | 生成 tok/s |
|---|---|---|---|---|
| lfm2.5-1.2b（CPU 常驻） | 0 | | | 77 |
| qwen3.5-2b + mmproj | 2542 | 3609 | 655 | 119 |
| qwen3.5-4b | 3396 | 2351–4056 | 675 | 62 |
| qwen3.5-4b + mmproj | 4262 | 3199 | 672 | 62 |
| granite-h-micro | 2348 | 1522–2767 | 712 | 81 |
| gemma4-e2b | 1676 | 1929–2351 | 691 | 107 |
| nemotron-nano-4b | 3149 | 1929 | 646 | 69 |
| qwen3.5-9b IQ4_XS | 5187 | 2812 | 709 | 41 |

桌面基线 1.43G。gemma 估 3.9G 实际 1.7G，9B 估 6.1G 实际 5.2G，其余差不多。specialists 的换入链 reasoning→motor→verifier→language 跑一遍，LRU 按预期卸最久没用的，nvidia-smi 峰值 7459 MB（增量 6031，预算 6400 以内）。不够的话 `-ctk q8_0 -ctv q8_0` 把 KV 砍半。

内存方面所有 GGUF 走 mmap，0.56+1.28+2.74+0.67+5.17 大概 10.4G 全钉在页缓存里，32G 绰绰有余，所以换模型永远是热加载。

加载延迟见上表：热加载 1.5 到 4 秒（含 router 起子进程），卸载 0.7 秒左右，一次任务换三四个模型大约多花 10 秒。

缓存策略：启动时把所有 GGUF 顺序读一遍钉进页缓存；模型管理器按显存预算而不是按个数做 LRU；验证器连续两次 CONFLICT 就提前开始加载 9B；共享的 system prompt 前缀靠 llama-server 自带的 prompt cache 复用。

## 选哪些模型

今天在 HF 上一个个查过 GGUF 存在和体积。`specialists` 这套尽量一个 lobe 一家：

| lobe | 模型 | 家 | 文件 | 放哪 |
|---|---|---|---|---|
| executive 执行中枢 | LFM2.5-1.2B-Instruct | Liquid | Q4_K_M 0.73G | CPU 常驻，永不卸载 |
| perception 感知 | Qwen3.5-2B + mmproj | Qwen | 1.28G + 0.67G | GPU 换入 |
| reasoning 推理 | Qwen3.5-4B | Qwen | Q4_K_M 2.74G | GPU 换入 |
| motor 工具 | granite-4.0-h-micro | IBM | Q4_K_M 1.94G | GPU 换入 |
| language 语言 | gemma-4-E2B-it | Google | Q4_K_M 3.11G | GPU 换入 |
| verifier 验证 | gemma-4-E2B-it（原定 Nemotron-3-Nano-4B，盲解答出标点，换了，见 DECISIONS） | Google | 同上 | GPU 换入 |
| escalate 升级 | Qwen3.5-9B / 远端 API | | IQ4_XS 5.17G | 全卸了再进 |

六个 lobe 五家。只有感知和推理都是 Qwen，因为 mmproj 是跟模型走的，而且 2B 看截图明显比 LFM2.5-VL-1.6B 强，这个地方不值得为了多样性牺牲。granite 用 h-micro 不用 micro，h 是混合 Mamba2 结构，KV 小得多。Nemotron-3-Nano-4B 也是混合 Mamba-Transformer（42 层只有少数是注意力）。gemma-4-E2B 一个 KV 头，KV 也小。

`shared` 这套：executive 还是 LFM2.5-1.2B 在 CPU，perception 挂 4B 的 mmproj，reasoning / motor / language / verifier 全是 Qwen3.5-4B 换提示词，verifier 走盲解。常驻 4.6G 不换。

Qwen3.5 的 thinking 用 `chat_template_kwargs: {enable_thinking: false}` 关，原生支持工具调用，上下文 262K。Nemotron 也有 reasoning on/off 两种模式。远端 provider（Codex / OpenAI / Claude / DeepSeek）可以顶替任何一个 lobe。V3 想试的：LFM2.5-8B-A1B（Q4 5.16G，激活 1B）放 CPU 当第二意见；nomic-embed（本机已有）做记忆。

不打算用的：xLAM-2 那类专用函数调用模型（2025 年 Llama-3.2 底子，有了语法约束之后没优势）；Transformers 加 bitsandbytes（Windows 8G 下比 GGUF 又慢又费显存）；vLLM（Windows 支持差）。模型名只出现在配置里，llama.cpp 的 README 里已经出现 Qwen 3.6 的字样了，到时候改配置就行。

## 谁常驻谁换入

`specialists` 下一个普通文本任务的走法：executive（CPU）看一眼决定要不要工具和推理 → reasoning 4B 进 GPU（3.6G）→ 要调工具就把 motor granite 也进来（1.9G 加 KV，两个一起 5.8G，挤得下）→ 验证和 language 都是 gemma 1.7G，进来时按 LRU 把 motor 卸掉，4B 留着。一趟下来换两次，加载开销四五秒。（第一版验证用 Nemotron 3.2G，进不来要把 4B 也卸掉，推理验证之间来回换，一道乘法题换了 9 次 58 秒，所以改了。）这就是 `specialists` 的代价，评测会把它记下来。executive 能自己答的小问题走快速路径，一次模型都不换。

`shared` 下 4B 常驻不动，只有升级 9B 时全卸。

部分卸载到 CPU 只在跑 gemma-4-12B 基线时允许，正常路径禁用，太慢。

## 代码怎么组织

两个进程。llama-server router 模式一个，每个模型它自己起子进程。lobes 一个 Python 进程，V0 是 CLI，V1 变成常驻服务并对外开一个 OpenAI 兼容的 `/v1/chat/completions`，这样 Codex CLI、Open WebUI、VS Code 插件都能把 Lobes 当成一个模型来用。

接入其他 AI 的关键是 provider 这一层。一个接口：

```
chat(messages, schema=None, tools=None, images=None, thinking=False) -> Envelope
```

OpenAI 兼容的适配器一个就覆盖 llama-server、LM Studio、OpenAI、Codex、DeepSeek、OpenRouter。Anthropic 单写一个，六十行左右。配置长这样：

```yaml
providers:
  local:     {type: openai, base_url: http://127.0.0.1:8080/v1}
  lmstudio:  {type: openai, base_url: http://127.0.0.1:1234/v1}
  openai:    {type: openai, base_url: https://api.openai.com/v1, api_key: ${OPENAI_API_KEY}}
  deepseek:  {type: openai, base_url: https://api.deepseek.com/v1, api_key: ${DEEPSEEK_API_KEY}}
  anthropic: {type: anthropic, api_key: ${ANTHROPIC_API_KEY}}
roles:
  main: local/qwen3.5-4b
  fast: local/qwen3.5-0.8b
  verifier: local/qwen3.5-4b
  escalate: [local/qwen3.5-9b, openai/gpt-5.x]
```

key 只放 .env，不进 git。`lobes providers test` 挨个打一下看通不通。

模型管理器就是一张表：name、file、vram_est、resident、loaded、last_used。`ensure(name)`：没加载就按 LRU 卸非常驻的直到预算够，然后 `/models/load`，轮询到就绪。加载完读一次 nvidia-smi 把 vram_est 校准掉。

路由器是个纯函数 `route(state)`，状态机：INTAKE → FAST 或 PLAN → ACT → TOOL → VERIFY → 回答 / 重试回 ACT / 再跑工具 / 升级后回 ACT。最多 8 步、2 次重试、1 次升级。

没有消息总线。单进程函数调用传信封，每个信封追加写到 `runs/<task_id>/trace.jsonl`。多进程总线现在是过早设计。

任务状态一个对象：goal、信封历史、产物、断言表、预算计数，每步落盘。

工具就是带 pydantic 参数的 Python 函数，注册表导出 JSON schema 给主模型做约束选择。V0 只有 `python`（子进程，10 秒超时，工作目录隔离）和 `read_file`。Windows 上做不出真沙箱，V2 再考虑独立 venv 加 Job Object 或者 Docker。

日志就是 trace.jsonl 加 rich 打到终端，每次 LLM 调用记 model、tokens、延迟、显存快照。上下文超长的工具输出交给 0.8B 摘要。V0 没有长期记忆。

栈：Python 3.12，httpx、pydantic v2、typer、rich、pyyaml、python-dotenv。V1 加 fastapi 和 uvicorn，评测加 datasets。不用 LangChain 和 LangGraph，它们把控制流藏起来，而控制流正是我要测的东西。llama.cpp 用 b10951 的 win-cuda-13.3 包（150M，cudart 另 391M，610 驱动支持 13.x）。

```
Lobes/
  README.md  docs/ARCHITECTURE.md  lobes.yaml  .env.example  pyproject.toml
  lobes/   cli.py config.py providers.py schema.py models.py router.py runner.py verify.py log.py
           tools/python_exec.py tools/files.py
  presets/models.ini
  scripts/install.py      下载 llama.cpp 和 GGUF
  eval/    suites/ run_eval.py report.py
  tests/   test_smoke.py
  runs/  models/          不进 git
```

## 分几步做

V0：install 脚本，provider 层，schema，4B 加 0.8B，python 工具，盲解验证，CLI，评测骨架加 9B 基线。做完的标志是 `lobes ask "…"` 能跑，GSM8K 50 题、HumanEval 30 题、自己写的 20 个工具题上有 A/B 数字。

V1：完整的验证阶梯（证据、盲解、一致性），重试策略，置信度合成，升级到 9B 和远端 API，常驻服务加 OpenAI 兼容端口。

V2：视觉（mmproj 加截图工具），shell、文件编辑、网页抓取，工具输出和断言的字面比对。

V3：按显存预算的 LRU 和预测性预加载，异家族裁判，CPU 上跑 MoE，按任务类别用评测数据决定验证器开关，用自己攒的 trace 微调 0.8B 当路由或裁判（Unsloth LoRA，8G 能做）。这是唯一一条能让「小专用模块」真的胜过「小通用模型换个提示词」的路。

## 评测

先把假设改一下。原假设「模块化小模型组合优于同预算单模型」在纯推理和编码准确率上大概率不成立，9B 会赢 4B 加 0.8B 加裁判。但在可靠性上可能成立：幻觉率（断言接地）、工具调用合法率（按构造就是 100%）、多步任务完成率（有重试）、成本可控（有快速路径）。所以改成：模块化买到的是可靠性和成本控制，不是智力。

最要紧的一点：语法约束、工具、重试这些脚手架不是 LLM，单模型也能用。基线必须拿到一模一样的脚手架，不然测出来的是脚手架的功劳，不是模块化的。

条件：A 是 9B 加同样脚手架（同预算单模型），B 是 4B 加脚手架，C 是 B 加 0.8B，D 是 C 加验证阶梯，E 是 D 加动态升级到 9B（完整的 Lobes），F 是远端 API 当天花板。

测什么：编码用 HumanEval+ 和 MBPP+ 子集看执行 pass@1；推理用 GSM8K、MATH-500 子集、GPQA-Diamond 子集；图像用 MMMU 子集、ChartQA、OCRBench 子集加自己截 20 张 UI 图；工具用 BFCL-v4 子集加自己写的 30 个真实多步任务，看调用合法率和成功率；幻觉用 SimpleQA 子集加断言标注，看无支持断言的比例和弃答率；任务完成用那 30 个多步任务人工二值判；延迟记 p50 p95 端到端和分阶段；显存用 nvidia-smi 每 100ms 采样取峰值；多步可靠性是同一批任务跑三个种子看方差和卡死循环率。

每个条件至少三个种子，报置信区间。同时报每千 token 准确率和每秒准确率，并且给单模型同等算力的 self-consistency 多次采样，不然很容易变成「模块化只是多花了三倍 token」。跑之前把假设和阈值写死在 eval/PREREG.md 里。

## 别人做过的

最像的是 HuggingGPT（2023，LLM 当控制器调度专家模型）和 Mixture-of-Agents（2024）。NVIDIA 2025 有篇「Small language models are the future of agentic AI」讲的就是这个方向。模型级的级联和路由有 FrugalGPT、RouteLLM，speculative decoding 也是小大配对的思路。验证这块：Cobbe 2021 的 GSM8K verifier，Let's Verify Step by Step 的过程奖励模型，CRITIC 用工具做批评，Self-Refine、Reflexion，self-consistency，Zheng 2023 讲 LLM 裁判的偏差，Burns 2023 的 weak-to-strong，Du 2023 的多智能体辩论。工具这块 ReAct、Toolformer、PAL、Gorilla 和 BFCL。认知架构有 Minsky 的 Society of Mind、ACT-R、SOAR、Global Workspace、CoALA。MoE（Switch、Mixtral）是 token 级网络内联合训练的路由，跟这里的模块级路由不是一个层面。「compound AI systems」是 BAIR 2024 给这类东西起的名字，最贴切。

老实说脑区这个比喻有个漏洞：脑区是一起训练出来的，有共享表征；我这几个模型不是，中间传的是有损的文本。所以真正能分的区只有感知（ViT）、语言和推理（LLM）、运动（工具执行）、执行控制（状态机）、还有随叫随到的更大的脑（升级）。V3 拿自己的 trace 去微调小模型，才算是往真正的分区走了一步。

## 最后定的 V0

```
用户 → executive（LFM2.5 在 CPU，规则 + 小模型）
          ├─ 小问题：自己答，完事
          └─ 否则：拆步 →
               perception（Qwen3.5-2B，有图才进）
               reasoning（Qwen3.5-4B，按 schema 出断言 / 工具请求）
               motor（granite，把工具请求变成合法调用）→ python 工具 → 代码做证据比对
               verifier（gemma，证据检查 + 盲解）→ PASS / RETRY / VERIFY_WITH_TOOL / CONFLICT
               language（gemma，把信封写成人话；shared 下是 passthrough）
               CONFLICT → 换入 9B 或远端 API
模型管理器按显存预算 LRU 换入换出，每次换都记时间和 nvidia-smi
```

选它的理由：六个脑叶都在，每个是真的独立模块，权重各家各用，换入换出是常态而不是摆设，这才是最初想做的东西。同时每个 lobe 都能在 yaml 里换成共用权重、非 LLM 实现或远端 API，所以「专业化值不值」是跑出来的数字，不是信仰。验证靠执行、字面比对和盲解，弱裁判附和的问题结构上就不存在。评测把脚手架和模块化分开，能真的回答「模块化到底买到了什么」。
