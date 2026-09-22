# Lobes 上手

中文 | [English](GUIDE.en.md)

README 里只有三行命令。这篇把每一步拆开讲，再写一下我是怎么把 Lobes 接到 Codex 和 DeepSeek Harness 上的。第一次装照着走就行。

## 需要什么

一张 NVIDIA 显卡，Python 3.11 以上。8 GB 显存能跑默认的 `specialists`。显存更小的话，要自己把 `lobes.yaml` 里的 `llama.vram_budget_mb` 和每个模型的 `vram_mb` 调小。

Windows 上 Lobes 会下 llama.cpp 的预编译包，不用装编译器。Linux 上它要从源码编 `llama-server`，所以 `git`、`cmake`、`nvcc` 都得在 `PATH` 里，少一个安装就会卡在那一步。

## 安装

```bash
pip install -e .
lobes install
```

`lobes install` 会下载 llama.cpp 和当前 profile 用到的模型，然后写出 `models/models.ini`。模型加起来有好几个 G，第一次要等挺久。下到一半断了就再跑一遍，已经下完的文件不会重新下。

只下某一个 profile 的模型：

```bash
lobes install --profile specialists
```

如果 llama.cpp 你已经自己放好了，加 `--skip-llama`。

还有两个可选的包：

```bash
pip install -e ".[ocr]"        # 多一个 RapidOCR，用来读图里的字
pip install -e ".[dev,eval]"   # 跑测试和评测要用
```

## 启动服务

```bash
lobes serve
```

这条命令会一直占着终端，别关。它在 8080 端口起 llama.cpp 的 router。router 启动时一个模型都不加载，等请求要用哪个再装哪个、卸哪个。每次 `lobes serve` 启动都会重写 `models/models.ini`，所以改了 `lobes.yaml` 里的模型之后重启一下就行。

## 问一个问题

另开一个终端：

```bash
lobes ask "用 python 给我写一个贪吃蛇游戏"
```

第一次会慢，因为要先把模型装进显存。之后只要模型还在显存里，下一个问题马上就开始答。

带图片问：

```bash
lobes ask --image shot.png "这个屏幕上是什么"
```

想让它多想一会儿：

```bash
lobes ask --effort high "把这个 CSV 按第三列排序，再算每组均值"
```

`--effort` 有 low、medium、high 三档，默认 medium。档位改的是 reasoning 每次调用能想多久，以及整个请求的上限。用哪些模型、按什么顺序跑都不变。

看现在显存里装着哪些模型：

```bash
lobes models
```

## 接 Codex

先把 API 起起来：

```bash
lobes api
```

它在 8090 端口开三个接口：`/v1/chat/completions`、`/v1/responses`、`/v1/models`。Codex 用的是 `/v1/responses`。

然后在 `~/.codex/config.toml` 里写：

```toml
model = "lobes/specialists"
model_provider = "lobes"
model_reasoning_effort = "medium"

[model_providers.lobes]
name = "Lobes"
base_url = "http://127.0.0.1:8090/v1"
wire_api = "responses"
```

`base_url` 写到 `/v1` 就可以了。`/responses` 是 Codex 自己加的，你写全了反而会 404。

`model` 填的是 profile，格式是 `lobes/<profile>`。`lobes.yaml` 里现在有 `specialists`（默认）、`shared`、`v1`、`bare-9b`、`bare-4b`、`single-9b`、`single-4b`。写 `lobes-v1` 也行，等于 `v1`。

`model_reasoning_effort` 对应上面那三档。`auto` 算 medium，`xhigh` 和 `max` 算 high。Codex 有时会发 `minimal`，Lobes 不认识，就用 `lobes.yaml` 里的默认档。

这里没写 `env_key`，因为 API 没有鉴权，根本不看 key。如果你的客户端非要填一个，随便写什么都行。

Codex 自己带了工具的时候，工具调用会发回给 Codex，由 Codex 去跑。

## 接 DeepSeek Harness

Harness 用的是 chat completions，所以也连 8090 这个端口。

在 `$DSH_HOME/settings.yaml` 里加上：

```yaml
llm-pi-ai:
  providers:
    lobes:
      api: openai-completions
      baseURL: http://127.0.0.1:8090/v1
      models:
        - id: lobes/specialists
```

加完以后模型选择器里就会出现这个 provider。选一次，之后新会话默认就用它。

Harness 每一轮都会把工作区和策略的快照当成一条 user 消息发过来。Lobes 认得这条消息（`api.py` 里的 `CONTEXT` 常量），会把最新的那份放进 system prompt，不会把它当成一个问题去回答。

## 出问题了先看这里

`lobes ask` 连不上 8080 的话，多半是 `lobes serve` 没开。

显存不够时，模型管理器会直接报错，不会超预算。可以把 `vram_budget_mb` 调小，或者给某个模型加 `resident: true`，让它一直不被卸掉。

每个请求的完整过程都写在 `runs/<task_id>/trace.jsonl` 里，一行是一步，每个工具的原始输出另外存成一个 json。答案不对的时候，先看这个文件。

另外记得 python 和 shell 工具没有沙箱，README 里的警告写了这意味着什么。
