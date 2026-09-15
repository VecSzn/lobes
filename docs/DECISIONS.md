# 决定记录

一行一条，倒序。写这个是因为过两周我自己都会忘了当时为什么这么选。

- 2026-09-14 发布内容：仓库只放代码、题目清单、PREREG、REPORT 和 README 的结果表；每题记录（eval/results/*.jsonl）和 trace 不进 git，发布时打包挂 release。参考的是 Open LLM Leaderboard 把 details 放独立数据集、lm-eval-harness 的 log_samples 只推 Hub 不进代码仓库。push 前用 filter-branch 把早先进过历史的 jsonl 清掉。
- 2026-09-14 `effort: auto` 从 medium 起，重试用完先升 high 再 xhigh，升完才换大模型：多想一会儿比换模型便宜。
- 2026-09-14 加 effort 档位（low/medium/high/xhigh/max）：想不想、想多少 token、投票抽几个、重试几次、最多几步、要不要升级，全挂在 runner.py 的一张表上，medium 就是原来写死的那组数。入口是 lobes.yaml 的 `effort:`、`lobes ask --effort`、api 的 `reasoning_effort`。5090 上的 v1/v2 对比全在 medium，另跑一组 D 的 high 单独列。
- 2026-09-14 v2 起点：4070 上 v1 跑到 A、D 全量、E 一半时看了 trace，改动都对着具体失败项，规则写在 eval/PREREG-v2.md，v1 代码打了 tag `v1-4070`，两版都在租的 5090 上重跑才能比。评测结果目录加 `--tag`，一版一个目录。
- 2026-09-14 SimpleQA 改成有话直说：最终没过验证、或 PASS 但 basis 是 none 的答案，language 前面加 "Not sure. Best guess: "。v1 的弃权正则本来就抓 "not sure"，所以判分代码不动，报表多一列"答了且对"（correct 且没弃权）。v1 里 D 的 SimpleQA 弃权 0%、答了且错 63%，verifier 那条盲解在闭卷常识题上就是两个小模型互相猜，没意义。
- 2026-09-14 闭卷 qa（没工具输出、没图）reasoning 一律 3 采样：0.2 一个、0.7 两个，三个一致才给 basis consistency，否则 none 走 hedge。代码题如果题干自带 `>>>` 例子，第一个样本先过例子，过了就用，不过再抽两个挑过得最多的。用户要的"多跑几遍选最好"就是这两条，别的题不多跑。
- 2026-09-14 verifier 代码路径：先跑题干自带的 doctest 例子（代码判，不叫模型）；没例子才让模型写测试，而且不给它看代码，只给定义了哪些名字。v1 里 humaneval D 的 5 个错答案是"模型没写测试就 PASS"，4 个是看着代码写的弱测试放过了错代码。测试自己崩（NameError 在测试那几行）不算候选的错，按 `<string>` 行号分锅。
- 2026-09-14 `_compiles("61")` 是 True，所以 tools-08/-19 把一个数字当代码跑测试、PASS 了错答案。现在用 ast 看有没有 def/class/import/赋值/控制流才算代码。
- 2026-09-14 盲解和候选不一致又没东西可跑时：候选出现在某次成功的 stdout 里就 PASS（evidence），盲解答案出现在 stdout 里就换成盲解的（Verdict.answer，runner 套用）。tools-09 里 verifier 明明拿到了工具算出的正确答案，CONFLICT 却把错的候选留到最后。
- 2026-09-14 数字答案但所有 python 调用都挂了（多半是语法错）：evidence 直接 RETRY 一次让它修代码重跑。tools-02 就是工具挂了、reasoning 心算了个错数、gemma 返回空串被当弃权、PASS。
- 2026-09-14 python 工具：代码原样编译不过、把 `\n` `\t` `\"` 反转义后能编译就跑反转义的。granite 在 JSON 字符串里写字面量 `\n`，v1 里 A 43%、D 37% 的 python 调用死在这一个 SyntaxError 上。
- 2026-09-14 视觉链路改成三个读者：视觉模型描述+抄字、RapidOCR（代码，可选装 `lobes[ocr]`，结果按工具输出登记成 ocr_N 供 claim 引用）、verifier 再让视觉模型直接答一遍题。答案和 OCR 对上是 evidence，和第二眼对上是 consistency，第二眼和 OCR 对上就换答案。短边不到 768 的图先放大，v1 里 D 的 11 个 ocrbench 错题全是小图看错。
- 2026-09-14 reasoning 开思考后没返回 JSON（6000 token 用完），马上关思考再问一次。v1 里 A 的 SimpleQA 160 s/题大半耗在这。Reply 加 finish_reason 进 trace。
- 2026-09-14 Linux 上 b10951 没有 CUDA 预编译包（只有 cpu/vulkan/rocm/sycl），`lobes install` 在非 Windows 上 clone 对应 tag 用 cmake 编 llama-server（GGML_CUDA=ON，静态链接）。

- 2026-09-14 02:50 quick 计时：A（9B）平均 64 s/题，SimpleQA 上 160 s（盲解不一致后开思考，6000 token 用完还没答）；B（4B）24 s/题。按全量算 10 小时，超过一夜，按 PREREG 里写死的顺序砍：种子 1、2 只跑 multistep，GSM8K 和 SimpleQA 各 30 题，B3 不跑。预计 8 小时。改的是 eval.py 里的 N，判分和阈值没动。
- 2026-09-14 评测判分全是代码：GSM8K 取答案里最后一个数，HumanEval 跑官方 check()，SimpleQA 是标准答案字符串包含（官方用 LLM 判，我这个是下界），所以 SimpleQA 主要看“答了且错”的比例而不是准确率。等算力对照 B3 = 单 4B 从第一步就 3 采样投票，靠 cfg 的 vote 开关，不另写代码路径。
- 2026-09-14 单模型对照 A/B 直接当作 profile 写进 lobes.yaml（single-9b、single-4b），槽位填 none 表示没有。这样对照组和 specialists 走的是同一套 runner、语法约束、重试预算，代码里没有第二条路。
- 2026-09-14 verifier 盲解答“无法确定/could not be determined”时算弃权（PASS，basis 按有无工具证据），不再算 CONFLICT。gemma 对 fetch 任务连答 6 次同一句“无法确定”，把一个本来对的 Example Domain 送去了 9B 又送回来（193 s）。另外它提议的 python 检查如果只是 print 它自己的答案，当没提议。
- 2026-09-14 the 1.2B executive filed "take a screenshot" and "fetch <url>" as code, and the code path runs the answer as python, so both looped to the 9B and back (150 s, 227 s). Rule now: a tool verb in the goal downgrades the model's "code" to qa, and the code path only runs answers that compile.
- 2026-09-14 language 的改写要过代码检查：数字集合（去掉 goal 里出现的）必须和草稿一致，草稿 6 个词以内还得原样出现在改写里，否则丢掉改写用草稿。起因是 gemma 把验证过的 97405784 改写成了 97404784，等于最后一步把前面全白干了。
- 2026-09-14 python 工具在 stdout 为空、代码里没有 print、退出码 0 时，把最后一行当表达式重跑一次打印出来。granite 和 gemma 都爱写 `17 * 23` 然后等结果，空输出会让证据检查卡死在 RETRY。
- 2026-09-14 升级梯子：无思考 → 开思考 → 3 个 0.7 温度样本投票 → 9B → 远端（yaml 的 remote 槽，没 key 就跳过）。远端那级没 key 没跑过。
- 2026-09-14 `lobes api` 用 starlette + uvicorn，不装 fastapi。就两个路由，fastapi 的东西一样没用上。deepseek 不认 json_schema，provider 加 `json: object` 走 json_object 加把 schema 贴进提示；没 key 没验证。
- 2026-09-14 specialists 的 verifier 从 nemotron-nano-4b 换成 gemma4-e2b。同一份盲解提示 + JSON 语法下，nemotron 关思考 4 次里 2 次答 `,` 和 `: 391`，开思考把 goal 原句抄进 answer；granite 抄观察；gemma 4 次全答对还会提议 python 检查。副作用是 4B+gemma 一共 5.1G 能一起驻留，推理和验证之间不用来回换。nemotron 留在 yaml 里，评测阶段再比。
- 2026-09-14 验证 tool 类断言时，出现在 goal 里的数字不用工具输出背书，只查结果数。`17*23 = 391` 这种断言里 17 和 23 本来就不会出现在 stdout 里。
- 2026-09-14 verifier 的 PASS 只能由代码给：有工具证据是 evidence，盲解一致是 consistency，模型自己说“对”不算。Verdict.basis 记这个。
- 2026-09-14 8 个模型冒烟全过，显存按实测改了 yaml：gemma4-e2b 3900→1700，9B 6100→5200，其余小幅下调。qwen3.5-2b 开思考后 512 token 内没写完答案，perception 一律关思考。
- 2026-09-14 思考开关统一走 `chat_template_kwargs.enable_thinking`。Nemotron 3 Nano 的模型卡写的就是 `enable_thinking=False`，和 Qwen3.5 同一个机制，之前猜的 `/think` 系统提示删了。yaml 里 `thinking: true` 只表示这个模型有这个开关。
- 2026-09-14 `response_format` 用 OpenAI 形式 `{"type":"json_schema","json_schema":{"schema":...}}`。看了 b10951 的 server-common.cpp，两种写法都认，选 OpenAI 的是为了远端 provider 不用改。
- 2026-09-14 b10951 的 Windows 包里同时有 `llama-server.exe` 和 `llama.exe`，用前者，后者留着以防哪天前者没了。
- 2026-09-14 `lobes install --profile all` 而不是只装 specialists：shared 对照只多一个 4B 的 mmproj（0.67 GB），评测反正要用。
- 2026-09-14 yaml 流式映射里 `${VAR}` 必须加引号，`{` 会被当成 YAML 语法。
