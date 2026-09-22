import hashlib

import httpx
import pytest

from lobes import cli, install, models, providers


@pytest.mark.parametrize("schema", [None, {"type": "object", "required": ["answer"]}])
def test_length_exhaustion_does_not_make_another_request(monkeypatch, schema):
    sent = []
    usage = {"prompt_tokens": 12, "completion_tokens": 100, "total_tokens": 112}

    def post(url, json, timeout):
        sent.append(json)
        return httpx.Response(200, request=httpx.Request("POST", url), json={
            "choices": [{"message": {"content": "", "reasoning_content": "unfinished reasoning"},
                         "finish_reason": "length"}],
            "usage": usage,
            "timings": {"predicted_n": 100},
        })

    monkeypatch.setattr(providers.httpx, "post", post)
    reply = providers.chat({"base_url": "http://test/v1"}, "model", [{"role": "user", "content": "request"}],
                           schema=schema, thinking=True, max_tokens=100, ctx=64)
    assert len(sent) == 1 and sent[0]["max_tokens"] == 100
    assert reply.text == "" and reply.data is None
    assert reply.reasoning == "unfinished reasoning"
    assert reply.finish == "length"
    assert reply.usage == usage and reply.timings == {"predicted_n": 100}


def test_tools_and_thinking_budget_reach_the_server(monkeypatch):
    sent = []
    call = {"id": "c0", "type": "function", "function": {"name": "python", "arguments": "{\"code\": \"print(1)\"}"}}

    def post(url, json, timeout):
        sent.append(json)
        return httpx.Response(200, request=httpx.Request("POST", url), json={
            "choices": [{"message": {"content": None, "tool_calls": [call]}, "finish_reason": "tool_calls"}], "usage": {}})

    monkeypatch.setattr(providers.httpx, "post", post)
    spec = [{"type": "function", "function": {"name": "python"}}]
    reply = providers.chat({"base_url": "http://test/v1"}, "model", [{"role": "user", "content": "x"}],
                           thinking=True, thinking_budget=64, tools=spec)
    assert sent[0]["tools"] == spec and sent[0]["thinking_budget_tokens"] == 64
    assert reply.tool_calls == [call] and reply.text == ""
    providers.chat({"base_url": "http://test/v1"}, "model", [{"role": "user", "content": "x"}], thinking=False, thinking_budget=64)
    # some models think with the switch off, so an unasked thought still gets a small cap
    assert sent[1]["thinking_budget_tokens"] == providers.UNASKED_THOUGHT and "tools" not in sent[1]
    assert sent[1]["reasoning_budget_message"] == providers.BUDGET_MESSAGE
    # the shapes Ctx.chat sends: switched off with no budget still caps, switched on with none leaves the server's
    providers.chat({"base_url": "http://test/v1"}, "model", [{"role": "user", "content": "x"}], thinking=False)
    assert sent[2]["thinking_budget_tokens"] == providers.UNASKED_THOUGHT and sent[2]["reasoning_budget_message"] == providers.BUDGET_MESSAGE
    providers.chat({"base_url": "http://test/v1"}, "model", [{"role": "user", "content": "x"}], thinking=True)
    assert "thinking_budget_tokens" not in sent[3]


def test_a_call_cut_off_mid_arguments_goes_back_to_the_server_as_an_empty_object(monkeypatch):
    sent = []
    monkeypatch.setattr(providers.httpx, "post", lambda url, json, timeout: sent.append(json) or httpx.Response(
        200, request=httpx.Request("POST", url), json={"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}))
    cut = {"id": "c0", "type": "function", "function": {"name": "write_file", "arguments": '{"path": "a", "content": "12'}}
    whole = {"id": "c1", "type": "function", "function": {"name": "python", "arguments": '{"code": "1"}'}}
    history = [{"role": "user", "content": "x"}, {"role": "assistant", "content": "", "tool_calls": [cut, whole]}]
    providers.chat({"base_url": "http://test/v1"}, "model", history)
    assert [c["function"]["arguments"] for c in sent[0]["messages"][1]["tool_calls"]] == ["{}", '{"code": "1"}']
    assert history[1]["tool_calls"][0]["function"]["arguments"] == '{"path": "a", "content": "12'    # the caller's copy is untouched


def test_provider_does_not_retry_http_errors(monkeypatch):
    sent = []

    def post(url, json, timeout):
        sent.append(json)
        return httpx.Response(500, request=httpx.Request("POST", url))

    monkeypatch.setattr(providers.httpx, "post", post)
    with pytest.raises(httpx.HTTPStatusError):
        providers.chat({"base_url": "http://test/v1"}, "model", [{"role": "user", "content": "request"}],
                       thinking=True)
    assert len(sent) == 1


def _manager():
    return models.ModelManager({"llama": {"host": "test", "port": 8080, "vram_budget_mb": 100},
                                "models": {"present": {"vram_mb": 100}, "missing": {"vram_mb": 100}}})


def test_missing_router_model_fails_before_eviction_or_load(monkeypatch):
    manager = _manager()
    checked = []

    def status():
        checked.append(True)
        return {"present": "loaded"}

    monkeypatch.setattr(manager, "status", status)
    monkeypatch.setattr(manager, "_free", lambda need: pytest.fail("must not evict a model"))
    monkeypatch.setattr(models.httpx, "post", lambda *a, **kw: pytest.fail("must not load a model"))
    with pytest.raises(RuntimeError, match="missing.*restart `lobes serve`"):
        manager.ensure("missing")
    assert len(checked) == 1 and not manager.events


def test_validate_checks_one_roster_and_lists_all_missing_models(monkeypatch):
    manager = _manager()
    checked = []

    def status():
        checked.append(True)
        return {"present": "loaded"}

    monkeypatch.setattr(manager, "status", status)
    monkeypatch.setattr(models.httpx, "post", lambda *a, **kw: pytest.fail("validation must not load a model"))
    with pytest.raises(RuntimeError, match="missing, other.*restart `lobes serve`"):
        manager.validate(["present", "other", "missing", "missing"])
    assert len(checked) == 1
    assert manager.validate(["present"]) == {"present": "loaded"}
    assert len(checked) == 2 and not manager.events


def test_a_download_that_does_not_match_its_hash_is_thrown_away(tmp_path, monkeypatch):
    class Served:
        status_code, headers = 200, {"content-length": "4"}

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def raise_for_status(self):
            pass

        def iter_bytes(self, size):
            yield b"junk"

    monkeypatch.setattr(install.httpx, "stream", lambda *a, **kw: Served())
    dest = tmp_path / "model.gguf"
    with pytest.raises(RuntimeError, match="sha256"):
        install.download("http://test/model.gguf", dest, "0" * 64)
    assert not dest.exists() and not list(tmp_path.glob("*.part"))   # a rejected file leaves nothing to resume from
    install.download("http://test/model.gguf", dest, hashlib.sha256(b"junk").hexdigest())
    assert dest.read_bytes() == b"junk"


def test_unbuilt_source_tree_has_no_server(tmp_path):
    (tmp_path / "llama.cpp" / "examples" / "android" / "llama").mkdir(parents=True)
    assert install.find_server(tmp_path) is None
    server = tmp_path / "llama.cpp" / "build" / "bin" / "llama-server"
    server.parent.mkdir(parents=True)
    server.write_bytes(b"")
    assert install.find_server(tmp_path) == server


@pytest.mark.parametrize("stale", [False, True])
def test_server_command_rebuilds_presets_from_local_files(tmp_path, monkeypatch, stale):
    bindir = tmp_path / "bin" / "llama"
    bindir.mkdir(parents=True)
    executable = bindir / "llama-server.exe"
    executable.write_bytes(b"")
    modeldir = tmp_path / "models"
    modeldir.mkdir()
    (modeldir / "available.gguf").write_bytes(b"")
    preset = modeldir / "models.ini"
    if stale:
        preset.write_text("[stale-model]\nmodel = stale.gguf\n", encoding="utf-8")
    cfg = {"_root": tmp_path, "llama": {"host": "127.0.0.1", "port": 8080, "ctx": 16384},
           "models": {"available": {"file": "available.gguf", "device": "cpu", "resident": True, "kv": "q4_0"},
                      "unavailable": {"file": "unavailable.gguf"}}}
    monkeypatch.setattr(install, "download", lambda *a, **kw: pytest.fail("must not download"))
    command = cli._server_cmd(cfg)
    contents = preset.read_text(encoding="utf-8")
    assert command[0] == str(executable)
    assert command[command.index("--models-preset") + 1] == str(preset)
    assert "[available]" in contents and "n-gpu-layers = 0" in contents and "load-on-startup = true" in contents
    assert "cache-type-k = q4_0" in contents and "cache-type-v = q4_0" in contents and "flash-attn = on" in contents
    assert "[unavailable]" not in contents and "stale-model" not in contents
