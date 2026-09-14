# 决定记录

一行一条，倒序。写这个是因为过两周我自己都会忘了当时为什么这么选。

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
