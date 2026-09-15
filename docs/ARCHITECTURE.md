# Lobes 设计

这篇写的是现在的样子：六个脑叶各干什么、一次请求怎么走、值怎么定、档位和上限、代码怎么组织、评测怎么设。为什么改成这样、一路试过什么，在 docs/DECISIONS.md；数字在 eval/REPORT.md。

## 机器与后端

RTX 4070 Laptop，8188 MiB，桌面占 1.5 GB，模型预算 6400 MB（lobes.yaml 的 vram_budget_mb）。推理后端是 llama.cpp b10951 的 llama-server，router 模式：不指定模型启动，`--models-preset` 一个 ini 描述每个模型，`POST /models/load` / `/models/unload` 动态加载卸载，`--models-max` 限同时加载的个数。`response_format` 的 json_schema 做语法约束解码，mmproj 做多模态。Windows 取预编译包（win-cuda-13.3），Linux 从源码编。租的 5090 上预算 28000，全部常驻，executive 也上 GPU（eval/pod.sh）。

## 为什么是六个脑叶

几个小模型串起来，错误会叠加：每跳 90%，六跳剩六成。模块化只在两种情况下有意义：模块拿到的是不同的信息（图片、程序输出），或者干的是不同性质的活（生成对拿证据核对）。同一档小模型换个提示词不叫分区。由此：

- executive 不写计划，只分类。一个 1B 写的计划一旦摆给所有人，它对题的读法就成了全员前提（PREREG-v3 里 gsm8k-1252 那条路）。
- 每个脑叶是独立模块，有自己的输入输出契约；谁来干活是配置不是代码。砍的是权重不是模块：一个脑叶可以填一家的小模型、和别的脑叶共用权重，或者是纯代码。
- 答案不靠裁判。一个 1B 去审 4B 的答案，要么盖章要么加噪声；改成几个互相看不见的推导一致才算，程序打印的比模型写的可信。verifier 因此是代码。

## 脑叶

| lobe | 干什么 | specialists 里填的 | 家 | 放哪 |
|---|---|---|---|---|
| executive | 只分类：chat / math / code / qa，要不要程序；chat 当场答 | granite-4.0-h-1b Q8_0（1.56 GB） | IBM | CPU 常驻，永不卸载 |
| perception | 图片：描述一遍、抄出文字；再看图作答当证人，按档位思考 | Qwen3.5-2B Q4_K_M + mmproj（1.28 + 0.67 GB） | Qwen | GPU 换入 |
| reasoning | 主证人：思考，列出所求的值，交复算程序；题要源码时交代码 | Qwen3.5-4B Q4_K_M（2.74 GB） | Qwen | GPU 换入 |
| motor | 程序证人：只看题面写一段程序，不思考，输出就是值 | granite-4.0-h-micro Q4_K_M（1.94 GB） | IBM | GPU 换入 |
| verifier | 代码：跑代码题自带的例子；把值和 OCR 引擎读到的文字对上；一致判定 | 没有模型 | | |
| language | 措辞：把定下的值改写给用户，代码检查改写没丢东西 | gemma-4-E2B-it Q4_K_M（3.11 GB 文件，显存 1.7 GB） | Google | GPU 换入 |

三家。perception 和 reasoning 同是 Qwen，因为 mmproj 跟模型走，2B 看截图明显比同级别别家强；executive 和 motor 同是 IBM 不影响判定，executive 不当证人。granite 用 h-micro：混合 Mamba2，KV 小。Qwen3.5-9B（IQ4_XS 5.17 GB）只在评测里当基线，运行时不用；题再难也不叫更大的模型、不往外发。

第二套 profile `shared`：executive 不变，其余全是 Qwen3.5-4B（带 mmproj）换提示词，language 是 passthrough，常驻不换。评测里的 single-9b / single-4b 同理。模型名只出现在 lobes.yaml，换模型改配置。

## 显存

Qwen3.5 每四层一层全注意力，其余是 Gated DeltaNet，KV 每 token 4B 约 32 KB，2B 约 12 KB，所以几个模型能一起常驻。实测（2026-09-14，b10951，ctx 16384，4 slots，nvidia-smi 增量，加载后各生成 512 token）：

| 模型 | 显存 MB | 热加载 ms | 卸载 ms | 生成 tok/s |
|---|---|---|---|---|
| granite-1b Q8_0（CPU 常驻） | 0 | | | 22 |
| qwen3.5-2b + mmproj | 2542 | 3609 | 655 | 119 |
| qwen3.5-4b | 3396 | 2351–4056 | 675 | 62 |
| qwen3.5-4b + mmproj | 4262 | 3199 | 672 | 62 |
| granite-h-micro | 2348 | 1522–2767 | 712 | 81 |
| gemma4-e2b | 1676 | 1929–2351 | 691 | 107 |
| qwen3.5-9b IQ4_XS | 5187 | 2812 | 709 | 41 |

桌面基线 1.43 GB。specialists 的换入链 reasoning → motor → language 走一遍，LRU 卸最久没用的，峰值在 6400 预算内。GGUF 全走 mmap 钉在页缓存里（32 GB 内存够），换模型永远是热加载：1.5 到 4 秒一次，卸载 0.7 秒。模型管理器按显存预算做 LRU，常驻的永不卸，加载完读一次 nvidia-smi 校准估算；共享的 system prompt 靠 llama-server 的 prompt cache。不够就 `-ctk q8_0 -ctv q8_0` 把 KV 砍半。

## 一次请求怎么走

```
用户 → executive（CPU，只分类）
  ├─ chat：executive 自己答 → language
  ├─ 带图片：perception 描述 + OCR 引擎读，各自成一条带来源的观察
  │           证人：perception 看图作答（按档位思考）→ reasoning 读观察作答 → reasoning 热样本
  ├─ 要算的：reasoning（思考：值 + 复算程序）→ motor（不思考：程序）→ reasoning 热样本 …
  │           reasoning 交的是代码：题里的例子跑它，失败带原因重做，没有例子不查
  └─ 闭卷：reasoning n 个样本，连续 n 个一致才算
定下的值 → language 措辞 → 回复；定不下来 → 带 hedge 交出
```

证人拿到什么（`brief`）：题面；图片题另加观察（`[perception_0] (lobe:perception) …`、`[ocr_0] (tool:ocr) …`）；代码题重做时加一句上次为什么被拒。别的证人的计划、程序、输出一概不给，程序输出只进 trace 和 run 目录。

值的契约：一行一个，按题问的顺序，所求的排最后。两个证人一致（`agree`）：行数相等就逐行比，不等就短的要对上长的末尾（先打印中间量的只比末尾）；一个证人把几个值写在一行也按另一边的行数拆开比；数字比最后一个数，题面里给的数不算；文字去虚词后一边的词集包含另一边、或半数重合。

定案（`settle`）：两个一致。只要有证人跑过程序，一致的那一对里必须有跑过的（模型写的值压不过程序输出）。图片题一致的那一对里必须有 perception，或引擎读到同样内容的 ocr 证人：reasoning 每个样本读的是同一份笔记，它自己一致等于同一次阅读数两遍。闭卷题要最近 n 个样本连续一致。基由代码填：`evidence`（一对里有跑过的，或代码过了题里的例子）、`consistency`（都没跑）、`none`（定不了）。定不了案：交看得最多的那个证人的值（图片题 perception，其余 reasoning），language 前面加 "Not sure. Best guess: "，其余证人的值列在 uncertainties；代码前不贴前缀。

证人怎么产值：reasoning 按 schema 交 values / program / code；program 跑过打印的就是它的值，只打印自己字面量的不算跑过（`restates`：题面的数是输入，只有输出说明问题）；挂了带 stderr 修一次。motor 选一个工具调用（几乎都是 python），输出就是值，同样修一次。JSON 解析失败的证人没有值。executive 说 code 且第一个证人的值本身是源码时也走代码路径：executive 一个人判 code 不能路由，multistep、tools 里各有几道被它判成 code。

代码路径：第一个 reasoning 样本交回 code → verifier 跑题里的例子：`>>>` 行，没有就文档里 `f(x) => y`、`➞`、`==`、`==>` 这种写法；期望值能解析成字面量的按值比（`f(x) == want` 期望 True）。全过 basis evidence；没过带失败原因重做，最多 repairs 次；没有例子不查，basis none 交出去，不加前缀。盲写的测试删了（DECISIONS，09-15 下午）。

思考：reasoning 和 perception 按档位开，motor、executive、language 不开。思考撞预算就把已想的部分预填、加一句提示再要一次答案（trace 里 forced）；思考调用被服务拒掉就不思考再要一次（trace 记 refused；2B 的四个槽共用一个 16384 的统一 KV，四路带图各思考 6000 会溢出）。

language：只改措辞。改写丢了数字、把值从末尾挪走、少了草稿的任何一行、或开头是一个括号，就丢改写用草稿。裸数值不改写。

## 档位

| level | thinking | think tokens | 思考样本 n | repairs | cap: witnesses | cap: calls | cap: tokens | cap: seconds |
|--------|----------|--------------|-----------|---------|----------------|------------|-------------|--------------|
| low    | off      | 0            | 1         | 1       | 4              | 8          | 6000        | 120          |
| medium | on       | 6000         | 3         | 2       | 8              | 16         | 16000       | 300          |
| high   | on       | 16000        | 5         | 3       | 12             | 24         | 40000       | 600          |
| xhigh  | on       | 32000        | 8         | 4       | 18             | 36         | 80000       | 1200         |
| max    | on       | ctx          | 12        | 6       | 30             | 60         | none        | none         |

n 是 reasoning 的思考样本总数（第一个温度 0.2，其余 0.7）。四个上限在每个证人之间查，撞上就带 hedge 交卷，capped 记进记录。cap: tokens 按生成的 token 算（completion_tokens）：一次被截断的思考样本连预填重发在 total 上记两遍。xhigh 和 max 的 ctx 要比思考上限大。auto 从 medium 起，证人用完没定案就升 high、再 xhigh，不换模型。入口：lobes.yaml 的 `effort:`、`lobes ask --effort`、api 的 `reasoning_effort`。

## 模块之间传什么

```
Observation  source（lobe:perception | tool:ocr）· ref · summary
Witness      lobe · value（一行一个）· ran（值是程序打印的）· ref（tool_N）· note
Verdict      verdict: PASS | RETRY | CONFLICT · basis: evidence | consistency | none · failed_claims · notes
Envelope     kind · goal · answer · uncertainties[] · confidence{score, basis} · next{action}
```

证人之间传 Witness；Verdict 只有代码题的例子检查和 settle 在填，basis 由代码填，模型填不了；Envelope 是 language 交出去的最终信封，也是 api 的返回。schema.py 用 pydantic 定义，导出 JSON schema 给 llama-server 约束。

## 代码怎么组织

两个进程。llama-server router 一个（`lobes serve`），每个模型它自己起子进程；lobes 一个 Python 进程，CLI 有 install / serve / models / load / unload / ask / eval / api。`lobes api` 开一个 OpenAI 兼容的 `/v1/chat/completions`（:8090），别的客户端把 Lobes 当一个模型用。

provider 一个接口：`providers.chat(provider, model, messages, *, schema, images, thinking, temperature, max_tokens, seed, ctx) -> Reply`，OpenAI 兼容的适配器一个覆盖 llama-server 和 LM Studio。思考开关统一走 `chat_template_kwargs.enable_thinking`，每次调用显式发；每次调用换种子（seed 加调用序号），同一个种子下热样本会一模一样。

runner.py 一条直线：intake → fast 或 look → 证人循环（`_plan` 给顺序，`_need` 给要几个一致和锚，`settle` 定案，`capped` 查上限）→ 代码路径 `_code` → language。状态是一个 TaskState（goal、观察、证人、工具结果、判决、代码、计数）；每步追加写 `runs/<task_id>/trace.jsonl`（start / model / call / refused / intake / tool / witness / doctest / best_of / verdict / effort / language_rejected / final），程序和观察各存一个 json。没有消息总线，没有重试回路。

工具是普通函数（tools.py）：`python`（子进程 10 秒，工作目录隔离；JSON 里写成字面 `\n` 的程序先还原再编译）、`shell`、`read_file` / `write_file` / `edit_file`（只在工作目录）、`web_fetch`、`screenshot`（交给 perception）。注册表导出 JSON schema 给 motor 约束选择。没有真沙箱。

栈：Python 3.11+，httpx、pydantic v2、typer、rich、pyyaml、pillow；api 用 starlette + uvicorn；评测 pandas + pyarrow 读 parquet；OCR 可选 rapidocr-onnxruntime（`lobes[ocr]`）。不用 LangChain / LangGraph：控制流正是要测的东西，不能藏起来。

```
Lobes/
  README.md  README.zh-CN.md  lobes.yaml  pyproject.toml
  docs/    ARCHITECTURE.md  DECISIONS.md  img/
  lobes/   cli.py config.py providers.py schema.py models.py runner.py tools.py install.py api.py eval.py
           lobe/  executive.py perception.py reasoning.py motor.py verifier.py language.py
  eval/    suites/（题库 jsonl，make.py 算 tools 和 multistep 的答案）  PREREG.md PREREG-v2.md PREREG-v3.md REPORT.md
           plot.py（README 的图）  exp.py（证人样本的测量与回放）  pod.sh（租的 5090 怎么跑）
  tests/   test_lobes.py
  runs/  models/  eval/results/      不进 git
```

## 评测

问题只有一个：这样拆开，比一个什么都不套的裸 9B 强在哪。基线 R：Qwen3.5-9B 原样一次调用，无工具无脑叶，思考按模板默认，12000 token 上限，撞上限同样强制作答。对手 D：`specialists` 配置，中档和高档。早期还有 A（9B 加同样脚手架）、B（4B 加脚手架）、C（shared），只在 4070 和 5090 早期跑过局部，在 REPORT 附录。

题库：GSM8K 200、HumanEval 30、tools 60、multistep 60（后两个自己出，答案代码算死；已发的成绩跑的是前 30 道，后 30 道是 PREREG-v4 加的更难的一半，两半分开报）、SimpleQA 30、OCRBench 50（裸 9B 不看图，跑 320）。判分全是代码：GSM8K 比最后一个数，HumanEval 跑官方 check()，tools 和 multistep 看期望值在不在答案里，SimpleQA 字符串包含（下界，主要看答了且错的比例），OCRBench 包含。一个种子，每台机四题并行，秒数只在同样并发下可比。每题记对错、token、秒、证人、定案依据、forced、capped。

假设和阈值在跑之前写死在 eval/PREREG.md、PREREG-v2.md、PREREG-v3.md，文件冻结，偏离记在 REPORT。结果、逐题读法、证人统计、假设逐条成不成立，全在 eval/REPORT.md；README 只放最终一张表。

中档跑了两遍（同代码同机器）当误差棒，370 道差 5 道、320 道差 6 道、11 道翻面。这个带比大多数题库间的差距还宽，所以读任何一个成绩差之前先看它：成绩上裸 9B、中档、高档三者分不出来，能分出来的是成本（两遍差 0.9%）、tools 和 SimpleQA 的弃答行为。

## 别人做过的

最像的是 HuggingGPT（2023，LLM 当控制器调度专家模型）和 Mixture-of-Agents（2024）。NVIDIA 2025 有篇「Small language models are the future of agentic AI」讲的就是这个方向。模型级的级联和路由有 FrugalGPT、RouteLLM，speculative decoding 也是小大配对的思路。验证这块：Cobbe 2021 的 GSM8K verifier，Let's Verify Step by Step 的过程奖励模型，CRITIC 用工具做批评，Self-Refine、Reflexion，self-consistency，Zheng 2023 讲 LLM 裁判的偏差，Burns 2023 的 weak-to-strong，Du 2023 的多智能体辩论。工具这块 ReAct、Toolformer、PAL、Gorilla 和 BFCL。认知架构有 Minsky 的 Society of Mind、ACT-R、SOAR、Global Workspace、CoALA。MoE（Switch、Mixtral）是 token 级网络内联合训练的路由，跟这里的模块级路由不是一个层面。「compound AI systems」是 BAIR 2024 给这类东西起的名字，最贴切。

脑区这个比喻有个漏洞：脑区是一起训练出来的，有共享表征；这几个模型不是，中间传的是有损的文本。真正能分的区只有感知（ViT）、语言和推理（LLM）、运动（工具执行）、执行控制（分类器）。

## 没做的

多轮记忆、真沙箱、预测性预加载、用自己攒的 trace 微调小模型（这是「小专用模块」能真的胜过
「小通用模型换个提示词」的唯一路径，这次没走到）。并发只在所有模型都常驻时成立（5090 上评测
就是四道题一起跑的），8 GB 的换入换出一次只能一个请求。三个种子没跑，改成同一档同一份代码跑
两遍当误差棒（数字在 eval/REPORT.md）：一趟 370 道中档在 5090 上不到一小时，高档一小时三刻。
