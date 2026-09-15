# Lobes 设计笔记

2026-09-14，第一稿。想法是把几个小模型拼成一个「分区的脑子」，在一张 8G 的 4070 笔记本卡上跑。这篇先把原来的方案批一遍，再写我打算怎么做。09-15 按实际做出来的样子把过时的段落改了：第一稿怎么想的留着，后来没这么做的地方就地说明。

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

所以最后是这样：六个脑叶（lobe）全部保留，每个是独立模块，有自己的输入输出契约、schema 和日志。**谁来干活是配置，不是代码。** 每个 lobe 可以填一个专用小模型，也可以和别的 lobe 共用一份权重（第一稿还留了远端 API 的槽，2026-09-14 晚上连 key 一起删了，见 DECISIONS）。几个 lobe 还各有一个非 LLM 的实现：语言 lobe 有 `passthrough`（推理结果直接出）和 `llm`（改写）两种，验证 lobe 的证据检查本来就是代码。执行中枢原来有 `rules`（状态机）和 `llm`（规划器）两种，v3 之后只剩小模型分类这一件事（见 PREREG-v3），规则路由也拆掉了。

配置分两套 profile：`specialists` 每个 lobe 各用一家的模型，加起来远超 6.5G，所以换入换出是真的在跑；`shared` 几个 lobe 共用 4B，常驻不换，快。默认 `specialists`。评测直接比这两套加单个 9B，「专用小模型到底值不值」就变成实验结果而不是我拍脑袋。

## 验证怎么做才不是摆设

用一个 1B 去审 4B 的答案，结果要么盖章要么加噪声。LLM 当裁判的附和偏差是有论文的，小模型更严重。我的做法是让验证尽量不依赖裁判的意见：

能执行的就执行。代码跑一遍，数值重新算一遍。回答里引用了工具结果的地方，直接在代码里做字面比对（数字、字符串、退出码）。这一层不用任何模型。

盲解。验证器拿到题目但看不到候选答案，自己做一遍，然后代码比对两个答案。看不到答案就没有锚定，附和这件事从根上就不存在。代价是多一次生成。

把回答拆成断言。主模型输出的时候按 schema 拆成一条条原子断言，每条标来源：来自工具、推导出来的、还是假设的。验证器只需要审「假设的」那几条，活变小变具体。

验证器不能单独给 PASS。PASS 必须有证据（执行通过、盲解一致、工具比对通过）。验证器只有否决权：RETRY、CONFLICT、VERIFY_WITH_TOOL。弱模型的否决很便宜，它的赞同一文不值，那就设计成赞同不重要。

（v3 更正：上面三段是 v1/v2 的做法。5090 的 trace 说明「盲解」其实不盲：验证器看不到候选答案，但看得到执行叶的计划和按计划跑出的工具输出，答案就从那里回声回来；断言表也没人真的审。v3 把这一节换成一条规则：几个互相看不见的推导一致才算答案。推理叶开着思考，按题问的顺序列出所求的值并交一个复算程序；motor 叶从题面写一个程序，不思考；验证器（gemma，另一个家族，不思考）在前两个不一致时盲解第三次；还不一致就再抽推理叶的热样本，思考样本一共 n 个。程序打印的就是该证人的值（一行一个），逐个值一致即定案；有程序跑过之后，没跑程序的证人之间一致不算；JSON 解析失败的证人没有值。题要的是源码时推理叶交的是代码，走例子检查 / 盲测试。验证器只剩代码题的例子检查和盲测试，没有 RETRY / VERIFY_WITH_TOOL 循环。见 eval/PREREG-v3.md，和它的偏离记在 REPORT。09-15 上午试过推理叶先不思考、便宜的证人不一致才开思考：token 少三分之一，gsm8k 200 道掉 10 道，两段不思考的同家族程序会同读错一处，撤了，见 DECISIONS。）

校准没做成第一稿写的样子：v3 里验证器不下判决，能记的是每个题库上定案靠 evidence / consistency / none 各占多少、各自翻错多少，在 REPORT 里。

换个家族做裁判这条做了：验证叶是 gemma-4-E2B。LFM2.5 没进任何槽，1.2B 分类不行，8B-A1B 没试。

## 置信度、重试

模型自报的 0.0–1.0 不用。置信度只有三档来源：evidence（定案的一对里有程序跑过，或代码过了例子 / 盲测试）、consistency（都没跑程序但一致）、none（定不了案，带 hedge 交出推理叶的值，前缀 Not sure，信封上的 confidence 是 self 0.5）。第一稿想用的 logprobs 没用上。

重试只有两种，都带新东西：程序挂了带 stderr 修一次；代码题例子没过带失败原因重做一次。原样重跑没有。

升级阶梯（4B 不带 thinking → 带 thinking → 采样投票 → 换入本地 9B → 远端 API）是 v1 的东西，2026-09-14 晚上删了：9B 只在评测里当基线，远端槽和 key 一起删了。`--effort auto` 只是证人用完就升一档、多抽几个样本，不换模型。

证人不一致又没有证人可抽：把推理叶的值标上不确定交出去，别的证人的值列在 uncertainties 里。不强行给答案。

## 模块之间传什么

一种信封，pydantic 定义（schema.py），导出 JSON schema 给 llama-server 做约束。散文只允许出现在最终 answer 字段里。

```
Envelope   kind: step_result | final · goal · observations[source, ref, summary] · tool_calls[name, args]
           answer · uncertainties[] · confidence{score, basis: self | consistency | evidence | logprob} · next{action: tool | answer}
Witness    lobe · value（一行一个）· ran（值是程序打印的）· ref（tool_N）· note
Verdict    verdict: PASS | RETRY | CONFLICT · basis: evidence | consistency | none · failed_claims · notes
```

证人之间传的是 Witness，不是信封；Verdict 只有代码题的例子检查 / 盲测试和 settle 在填，basis 由代码填，模型填不了。第一稿里的 claims 断言表、plan / tool_result / verdict 三种 kind、verify / retry / escalate 三个动作和 budget 字段都删了，算账在 trace 里。

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
| lfm2.5-1.2b（CPU 常驻，已换掉） | 0 | | | 77 |
| granite-1b Q8_0（CPU 常驻） | 0 | | | 22 |
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

缓存策略：模型管理器按显存预算而不是按个数做 LRU，常驻的（executive）永不卸；GGUF 靠 mmap 进页缓存，交给操作系统，没有专门预读；共享的 system prompt 前缀靠 llama-server 自带的 prompt cache 复用。第一稿里「验证器连续两次 CONFLICT 就提前加载 9B」随升级一起删了。

## 选哪些模型

今天在 HF 上一个个查过 GGUF 存在和体积。`specialists` 这套尽量一个 lobe 一家：

| lobe | 模型 | 家 | 文件 | 放哪 |
|---|---|---|---|---|
| executive 执行中枢 | granite-4.0-h-1b（原定 LFM2.5-1.2B，341 条分类题最多对 157，换了，见 DECISIONS） | IBM | Q8_0 1.56G | CPU 常驻，永不卸载 |
| perception 感知 | Qwen3.5-2B + mmproj | Qwen | 1.28G + 0.67G | GPU 换入 |
| reasoning 推理 | Qwen3.5-4B | Qwen | Q4_K_M 2.74G | GPU 换入 |
| motor 工具 | granite-4.0-h-micro | IBM | Q4_K_M 1.94G | GPU 换入 |
| language 语言 | gemma-4-E2B-it | Google | Q4_K_M 3.11G | GPU 换入 |
| verifier 验证 | gemma-4-E2B-it（原定 Nemotron-3-Nano-4B，盲解答出标点，换了，见 DECISIONS） | Google | 同上 | GPU 换入 |

Qwen3.5-9B（IQ4_XS 5.17G）只在评测里当基线 R，运行时不用它。原来的 escalate / remote 两档 2026-09-14 晚上删掉了，见 DECISIONS。

六个 lobe 四家（executive 换成 granite 之后三家：Qwen、IBM、Google）。感知和推理都是 Qwen，因为 mmproj 是跟模型走的，而且 2B 看截图明显比 LFM2.5-VL-1.6B 强，这个地方不值得为了多样性牺牲；executive 不当证人，和 motor 同家不影响一致性判定。granite 用 h-micro 不用 micro，h 是混合 Mamba2 结构，KV 小得多。Nemotron-3-Nano-4B 也是混合 Mamba-Transformer（42 层只有少数是注意力）。gemma-4-E2B 一个 KV 头，KV 也小。

`shared` 这套：executive 还是 granite-1b 在 CPU，perception 挂 4B 的 mmproj，reasoning / motor / language / verifier 全是 Qwen3.5-4B 换提示词，verifier 走盲解。常驻 4.6G 不换。

Qwen3.5 的 thinking 用 `chat_template_kwargs: {enable_thinking: false}` 关，原生支持工具调用，上下文 262K。Nemotron 也有 reasoning on/off 两种模式。第一稿想在 V3 试的两样，LFM2.5-8B-A1B 放 CPU 当第二意见和 nomic-embed 做记忆，都没做；v3 做的是证人机制。

不打算用的：xLAM-2 那类专用函数调用模型（2025 年 Llama-3.2 底子，有了语法约束之后没优势）；Transformers 加 bitsandbytes（Windows 8G 下比 GGUF 又慢又费显存）；vLLM（Windows 支持差）。模型名只出现在配置里，llama.cpp 的 README 里已经出现 Qwen 3.6 的字样了，到时候改配置就行。

## 谁常驻谁换入

`specialists` 下一个普通文本任务的走法：executive（CPU）看一眼决定要不要工具和推理 → reasoning 4B 进 GPU（3.6G）→ 要调工具就把 motor granite 也进来（1.9G 加 KV，两个一起 5.8G，挤得下）→ 验证和 language 都是 gemma 1.7G，进来时按 LRU 把 motor 卸掉，4B 留着。一趟下来换两次，加载开销四五秒。（第一版验证用 Nemotron 3.2G，进不来要把 4B 也卸掉，推理验证之间来回换，一道乘法题换了 9 次 58 秒，所以改了。）这就是 `specialists` 的代价，评测会把它记下来。executive 能自己答的小问题走快速路径，一次模型都不换。

`shared` 下 4B 常驻不动。

部分卸载到 CPU 只在跑 gemma-4-12B 基线时允许，正常路径禁用，太慢。

## 代码怎么组织

两个进程。llama-server router 模式一个（`lobes serve`），每个模型它自己起子进程。lobes 一个 Python 进程：CLI 有 install / serve / models / load / unload / ask / eval / api，`lobes api` 对外开一个 OpenAI 兼容的 `/v1/chat/completions`（:8090），这样 Codex CLI、Open WebUI、VS Code 插件都能把 Lobes 当成一个模型来用。

provider 这一层一个接口：

```
providers.chat(provider, model, messages, *, schema=None, images=None, thinking=None, temperature, max_tokens, seed, ctx) -> Reply
```

OpenAI 兼容的适配器一个就覆盖 llama-server 和 LM Studio，两个都在本机；第一稿的 roles（main / fast / verifier）变成了 profile 里六个 lobe 各填一个 provider/model 或非 LLM 实现，见 lobes.yaml。`lobes providers test` 挨个打一下看通不通。

模型管理器就是一张表：name、file、vram_est、resident、loaded、last_used。`ensure(name)`：没加载就按 LRU 卸非常驻的直到预算够，然后 `/models/load`，轮询到就绪。加载完读一次 nvidia-smi 把 vram_est 校准掉。

第一稿的状态机（INTAKE → FAST 或 PLAN → ACT → TOOL → VERIFY → 回答 / 重试 / 升级）是 v1 跑的东西。现在 runner.py 是一条直线：执行叶分类（chat / code / math / qa，要不要程序）→ chat 由执行叶当场答；图片先让感知叶和 OCR 各读一遍，作为带来源的观察给证人 → 按 `_plan` 的顺序逐个叫证人，两个一致（闭卷 n 个连续一致）就停 → 语言叶措辞。推理叶交回代码而不是值时转入例子 / 盲测试，失败带原因重做一次。没有重试回路，单题有证人数、调用数、token、秒四个硬上限：

| level  | thinking | think tokens | 思考样本 n | repairs | cap: witnesses | cap: calls | cap: tokens | cap: seconds |
|--------|----------|--------------|-----------|---------|----------------|------------|-------------|--------------|
| low    | off      | 0            | 1         | 1       | 4              | 8          | 6000        | 120          |
| medium | on       | 6000         | 3         | 2       | 8              | 16         | 16000       | 300          |
| high   | on       | 16000        | 5         | 3       | 12             | 24         | 40000       | 600          |
| xhigh  | on       | 32000        | 8         | 4       | 18             | 36         | 80000       | 1200         |
| max    | on       | ctx          | 12        | 6       | 30             | 60         | none        | none         |

撞上限就带 hedge 交推理叶的值。思考撞 think tokens 上限时把思考截断、把已想的部分预填再要一次答案，所以一次被截断的思考样本在 token 账上记两遍（中档一个约 12.7k，高档约 32.8k）。5090 上中档 370 道有 27 道撞 token 上限、高档 21 道，全是四个证人四个值、第五个样本被截的题（REPORT 偏离 15）。xhigh 和 max 的 ctx 要比思考上限大。auto 从 medium 起，一档的证人用完还没定案就升 high、再 xhigh。

没有消息总线。单进程函数调用，每一步追加写到 `runs/<task_id>/trace.jsonl`（start / model / call / intake / tool / witness / doctest / best_of / verdict / effort / language_rejected / final）。多进程总线现在是过早设计。

任务状态一个对象（runner.State）：goal、观察、证人、工具结果、判决、代码、重试和 token 计数；trace 每步落盘。

工具是普通 Python 函数（tools.py），注册表导出 JSON schema 给运动叶做约束选择：`python`（子进程，10 秒超时，工作目录隔离）、`shell`（10 秒，删库类命令挡掉）、`read_file` / `write_file` / `edit_file`（只在工作目录里）、`web_fetch`（HTML 压成文本）、`screenshot`（交给感知叶）。真沙箱没做，Windows 上做不出来。

日志就是 trace.jsonl 加 rich 打到终端，每次 LLM 调用记 model、tokens、延迟，每次换模型记时间和 nvidia-smi。工具输出截断后原样进 trace，没有摘要模型；没有长期记忆，没有多轮。

栈：Python 3.12，httpx、pydantic v2、typer、rich、pyyaml、pillow；api 用 starlette 和 uvicorn（原打算 fastapi，两个端点用不上）；评测加 pandas 和 pyarrow 读 parquet（原打算 datasets，太重）；OCR 可选 rapidocr-onnxruntime。python-dotenv 随远端一起删了。不用 LangChain 和 LangGraph，它们把控制流藏起来，而控制流正是我要测的东西。llama.cpp 用 b10951：Windows 取 win-cuda-13.3 包（150M，cudart 另 391M，610 驱动支持 13.x），Linux 从源码编 llama-server。

```
Lobes/
  README.md  README.zh-CN.md  lobes.yaml  pyproject.toml
  docs/    ARCHITECTURE.md  DECISIONS.md  img/
  lobes/   cli.py config.py providers.py schema.py models.py runner.py tools.py install.py api.py eval.py
           lobe/  executive.py perception.py reasoning.py motor.py verifier.py language.py
  eval/    suites/（题库 jsonl，make.py 算 tools 和 multistep 的答案）  PREREG.md PREREG-v2.md PREREG-v3.md REPORT.md  plot.py pod.sh
  tests/   test_lobes.py
  runs/  models/  eval/results/      不进 git
```

## 分几步做，实际走成了什么样

第一稿排的是 V0（install、provider、schema、python 工具、盲解、CLI、9B 基线）→ V1（验证阶梯、升级到 9B 和远端、api）→ V2（视觉、更多工具、字面比对）→ V3（预测性预加载、异家族裁判、CPU 上跑 MoE、按类别关验证器、用 trace 微调 0.8B 当路由或裁判）。实际一天走了三版，见 README 的 Milestones 和 DECISIONS：

- v1（09-14 上午）：第一稿的 V0 加 V1 一口气做完，六个脑叶围着共享黑板，执行叶写计划，验证叶盲解，升级梯子和投票，api。4070 上跑，5090 上四个题库 74/90。
- v2（下午）：读 v1 的 trace 改的，规则在 PREREG-v2：验证叶用题目自带的例子和盲写的测试，effort 档位表，每次调用换种子，思考撞上限强制作答，`--workers` 并行，每模型 ctx。高档 82/90 对 9B 的 84/90。
- v3（晚上到 15 日）：黑板撤掉换成互相看不见的证人；执行叶换成 granite 1B 只做分类；梯子和远端槽删掉；一行一值逐行比；程序跑过的压不过；题库放大到 370 道。

第一稿 V3 那串里做了的只有异家族裁判（验证叶 gemma）和按预算的 LRU。没做的：预测性预加载、CPU 上跑 MoE、按类别用评测数据关验证器、多轮记忆、并发、用自己攒的 trace 微调小模型。最后一条仍是「小专用模块」能真的胜过「小通用模型换个提示词」的唯一路径，这次没走到。

## 评测

先把假设改一下。原假设「模块化小模型组合优于同预算单模型」在纯推理和编码准确率上大概率不成立，9B 会赢 4B 加 0.8B 加裁判。但在可靠性上可能成立：幻觉率（断言接地）、工具调用合法率（按构造就是 100%）、多步任务完成率（有重试）、成本可控（有快速路径）。所以改成：模块化买到的是可靠性和成本控制，不是智力。

最要紧的一点：语法约束、工具、重试这些脚手架不是 LLM，单模型也能用。基线必须拿到一模一样的脚手架，不然测出来的是脚手架的功劳，不是模块化的。

条件（eval.py 的 CONDITIONS）：R 是裸 9B，Qwen3.5-9B 原样一次调用、无工具无脑叶，这是 v3 起的尺子；A 是 9B 加同样脚手架，B 是 4B 加脚手架，B3 是 B 采样三次投票，C 是 `shared`（几个 lobe 共用 4B），D 是 `specialists`（完整的 Lobes）。第一稿的 E（动态升级到 9B）和 F（远端 API 天花板）随梯子和远端槽删了。5090 上跑全的只有 R 和 D；A/B/B3/C 在 4070 和 5090 早期跑过局部，数字在 REPORT。

题库：GSM8K 200、HumanEval 30、tools 30、multistep 30、SimpleQA 30、OCRBench 50（裸 9B 不看图，跑 320），一个种子，每台机四题并行。第一稿列的 MATH-500、GPQA、MMMU、ChartQA、BFCL、MBPP+ 和自己截的 UI 图都没上；三个种子和置信区间也没跑到，一趟 370 道中档在 5090 上要两个半小时，高档更久。每题记 token、秒、证人数、定案依据，报每题 token 和每题秒。假设和阈值在跑之前写死在 eval/PREREG.md、PREREG-v2.md、PREREG-v3.md 里，文件冻结，偏离记在 REPORT。

跑完的账（09-15，5090，每台四题并行，裸 9B 跑的 320 道）：v3 中档 261 对裸 9B 268，每题 token 8,981 对 13,436、秒 37.7 对 62.2；高档 267 对 268，token 14,375、秒 65.0。分题库：tools 两档都 30/30 对 25，multistep 25 平，GSM8K 177 / 182 对 184，HumanEval 27 / 28 对 29，SimpleQA 2 对 5（弃答 22 / 26，答错 5 / 3 对 9B 的 25），OCRBench 30/50（裸 9B 不看图）。上面那句"买到的是可靠性和成本控制，不是智力"：成本这半句在中档成立，高档不成立（token 反超 7%）；可靠性这半句只成立弃答那一截，一致判定没有校准出来——gsm8k 前两个证人一致时对 93%，不是 PREREG-v3 要的 97%，一致错的都是两个证人同读错一处题面；智力那半句如预期不成立，GSM8K 还是 9B 自己强。PREREG-v3 八条假设六条不成立，逐条在 REPORT。

## 别人做过的

最像的是 HuggingGPT（2023，LLM 当控制器调度专家模型）和 Mixture-of-Agents（2024）。NVIDIA 2025 有篇「Small language models are the future of agentic AI」讲的就是这个方向。模型级的级联和路由有 FrugalGPT、RouteLLM，speculative decoding 也是小大配对的思路。验证这块：Cobbe 2021 的 GSM8K verifier，Let's Verify Step by Step 的过程奖励模型，CRITIC 用工具做批评，Self-Refine、Reflexion，self-consistency，Zheng 2023 讲 LLM 裁判的偏差，Burns 2023 的 weak-to-strong，Du 2023 的多智能体辩论。工具这块 ReAct、Toolformer、PAL、Gorilla 和 BFCL。认知架构有 Minsky 的 Society of Mind、ACT-R、SOAR、Global Workspace、CoALA。MoE（Switch、Mixtral）是 token 级网络内联合训练的路由，跟这里的模块级路由不是一个层面。「compound AI systems」是 BAIR 2024 给这类东西起的名字，最贴切。

老实说脑区这个比喻有个漏洞：脑区是一起训练出来的，有共享表征；我这几个模型不是，中间传的是有损的文本。所以真正能分的区只有感知（ViT）、语言和推理（LLM）、运动（工具执行）、执行控制（分类器）。「随叫随到的更大的脑」删了。拿自己的 trace 去微调小模型才算往真正的分区走一步，这次没做。

## 现在的样子

```
用户 → executive（granite 1B 在 CPU，只分类：chat / code / math / qa，要不要程序）
          ├─ chat：自己答，完事
          ├─ 图片：perception（Qwen3.5-2B）描述一遍、OCR 引擎读一遍，作为带来源的观察给证人
          └─ 其余：证人逐个上，两个一致就停，看不到彼此
               reasoning（Qwen3.5-4B，思考）：所求的值 + 复算程序；题要源码就交代码 → 例子 / 盲测试
               motor（granite h-micro，不思考）：从题面写一个程序，输出就是值
               verifier（gemma E2B，不思考）：前两个不一致时盲解第三次
               reasoning 热样本：思考样本一共 n 个（中档 3，高档 5）
               闭卷题：只有推理叶，n 个样本连续一致才算
               language（gemma；shared 下 passthrough）：只管措辞，代码检查保住每个数字每一行
               定不了案：交推理叶的值，标上不确定
模型管理器按显存预算 LRU 换入换出，每次换都记时间和 nvidia-smi
```

第一稿定的 V0 图（执行叶 LFM2.5 规则加小模型写计划、motor 把工具请求变成合法调用、verifier 下 PASS / RETRY / VERIFY_WITH_TOOL / CONFLICT、CONFLICT 换入 9B 或远端 API）就是 v1 跑的东西，为什么一样样换掉在上面各节和 DECISIONS 里。

留着六个脑叶的理由没变：每个是真的独立模块，权重各家各用，换入换出是常态而不是摆设，这才是最初想做的东西。同时每个 lobe 都能在 yaml 里换成共用权重或非 LLM 实现，所以「专业化值不值」是跑出来的数字，不是信仰。答案靠互相看不见的推导一致和程序输出，弱裁判附和的问题结构上就不存在。评测拿裸 9B 当尺子，能真的回答「六个小模型到底买到了什么」。
