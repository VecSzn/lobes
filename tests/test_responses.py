import json

from starlette.testclient import TestClient

from lobes import api, config, runner
from lobes.providers import BUDGET_MESSAGE as BUDGET


def events(text):
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


CODEX = {
    "model": "lobes-v1", "instructions": "You are a coding agent.", "stream": True, "reasoning": {"effort": "minimal"},
    "tools": [{"type": "function", "name": "exec_command", "parameters": {"type": "object"}},
              {"type": "namespace", "name": "mcp__node_repl", "tools": [{"type": "function", "name": "js", "parameters": {}}]},
              {"type": "web_search", "external_web_access": False}],
    "input": [{"type": "message", "role": "developer", "content": [{"type": "input_text", "text": "skills"}]},
              {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "<environment_context/>"}]},
              {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "list the files"}]},
              {"type": "reasoning", "id": "rs_1", "summary": [{"type": "summary_text", "text": "notes"}]},
              {"type": "message", "role": "assistant", "phase": "commentary", "content": [{"type": "output_text", "text": "> thinking"}]},
              {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Listing."}]},
              {"type": "function_call", "call_id": "c1", "name": "exec_command", "arguments": '{"cmd": "ls"}'},
              {"type": "function_call", "call_id": "c2", "namespace": "mcp__node_repl", "name": "js", "arguments": "{}"},
              {"type": "function_call_output", "call_id": "c1", "output": "a.py"},
              {"type": "function_call_output", "call_id": "c2", "output": [{"type": "input_text", "text": "ok"}]}],
}


def test_codex_request_reaches_the_runner_as_chat_and_calls_come_back_as_items(tmp_path, monkeypatch):
    seen = {}
    state = runner.TaskState("t", "list the files", [])
    state.tool_calls = [{"id": "c3", "type": "function", "function": {"name": "mcp__node_repl__js", "arguments": "{}"}}]
    state.usage = {"prompt_tokens": 900, "completion_tokens": 40}

    def run(cfg, goal, **kw):
        seen.update(kw, goal=goal, effort=cfg.get("effort"))
        kw["on_delta"]("step", "[lobes] simple\n")
        kw["on_delta"]("reasoning", "\nThe user wants")
        kw["on_delta"]("reasoning", " js.")
        kw["on_delta"]("content", "Running js.")
        state.answer = "Running js."
        return state
    monkeypatch.setattr(api, "run", run)
    with TestClient(api.make_app(dict(config.load(), _root=tmp_path, effort="medium"))) as client:
        out = events(client.post("/v1/responses", json=CODEX).text)
    msgs = seen["messages"]
    assert seen["goal"] == "list the files" and seen["effort"] == "medium" and seen["cancel"].is_set()
    assert msgs[0] == {"role": "system", "content": "You are a coding agent.\n\nskills"} and [m["role"] for m in msgs[1:]] == ["user", "user", "assistant", "tool", "tool"]
    assert [c["function"]["name"] for c in msgs[3]["tool_calls"]] == ["exec_command", "mcp__node_repl__js"] and msgs[3]["content"] == "Listing."
    assert msgs[5]["content"] == "ok" and [t["function"]["name"] for t in seen["client_tools"]] == ["exec_command", "mcp__node_repl__js"]
    kinds = [e["type"] for e in out]
    assert kinds[0] == "response.created" and kinds[-1] == "response.completed"
    # codex draws only commentary messages as text and sends each delta to the item added last
    order = [(e["type"][9:], e["output_index"]) for e in out if "output_index" in e]
    assert order == [("output_item.added", 0), ("reasoning_summary_text.delta", 0), ("output_item.done", 0),
                     ("output_item.added", 1), ("output_text.delta", 1), ("output_text.delta", 1), ("output_text.delta", 1),
                     ("output_item.done", 1),
                     ("output_item.added", 2), ("output_text.delta", 2), ("output_item.done", 2),
                     ("output_item.added", 3), ("output_item.done", 3)]
    done = [e["item"] for e in out if e["type"] == "response.output_item.done"]
    assert [d["type"] for d in done] == ["reasoning", "message", "message", "function_call"] and out[-1]["response"]["output"] == done
    assert done[0]["summary"][0]["text"] == "[lobes] simple"
    assert done[1]["phase"] == "commentary" and done[1]["content"][0]["text"] == "> [lobes] simple\n> The user wants js."
    assert done[2]["phase"] == "commentary" and done[2]["content"][0]["text"] == "Running js."   # a tool call follows it
    assert done[3]["namespace"] == "mcp__node_repl" and done[3]["name"] == "js" and done[3]["call_id"] == "c3"
    assert out[-1]["response"]["usage"]["input_tokens"] == 900


def test_a_reviewed_answer_goes_out_as_a_message_once(tmp_path, monkeypatch):
    state = runner.TaskState("t", "hi", [])

    def run(cfg, goal, on_delta=None, **kw):
        if on_delta:
            on_delta("reasoning", "draft")
            on_delta("step", "\n[review] ")
            on_delta("reasoning", "OK")
        state.answer = "Hello. " * 300
        return state
    monkeypatch.setattr(api, "run", run)
    with TestClient(api.make_app(dict(config.load(), _root=tmp_path))) as client:
        out = events(client.post("/v1/responses", json={"model": "lobes-v1", "stream": True, "input": "hi"}).text)
        plain = client.post("/v1/responses", json={"model": "lobes-v1", "input": "hi"}).json()
    done = [e["item"] for e in out if e["type"] == "response.output_item.done"]
    assert [d.get("phase") for d in done] == ["commentary", None, "commentary", "final_answer"]
    assert [d["content"][0]["text"] for d in done[::2]] == ["> draft", "> [review]\n> OK"]
    answer = [e["delta"] for e in out if e["type"] == "response.output_text.delta" and e["item_id"] == done[-1]["id"]]
    assert "".join(answer) == done[-1]["content"][0]["text"] == state.answer and len(answer) == 50   # typed out, not one delta
    assert plain["output"][0]["content"][0]["text"] == state.answer and plain["status"] == "completed"


def stream(tmp_path, monkeypatch, deltas, draft, answer, body=None, stopped=False):
    """-> the done items of a /v1/responses stream whose run sends these deltas and ends with this draft and answer."""
    state = runner.TaskState("t", "q", [])

    def run(cfg, goal, on_delta=None, **kw):
        for kind, text in deltas:
            on_delta(kind, text)
        state.draft, state.answer, state.stopped = draft, answer, stopped
        return state
    monkeypatch.setattr(api, "run", run)
    with TestClient(api.make_app(dict(config.load(), _root=tmp_path))) as client:
        out = events(client.post("/v1/responses", json=body or {"model": "lobes-v1", "stream": True, "input": "q"}).text)
    return [(d.get("phase") or d["type"], d["content"][0]["text"] if d["type"] == "message" else d["summary"][0]["text"])
            for d in (e["item"] for e in out if e["type"] == "response.output_item.done")]


def test_a_thought_that_became_the_answer_is_shown_once(tmp_path, monkeypatch):
    # the reply ended inside its thinking and the re-ask came back empty. the thought used to show twice
    thought = "Windows 10, from the path." + BUDGET
    assert stream(tmp_path, monkeypatch, [("step", "[lobes] simple"), ("reasoning", thought)], thought,
                  "Windows 10, from the path.") == [("reasoning", "[lobes] simple"), ("final_answer", "Windows 10, from the path.")]


def test_an_answer_cut_off_by_a_cap_keeps_the_text_it_sent(tmp_path, monkeypatch):
    assert stream(tmp_path, monkeypatch, [("content", "The first half"), ("step", "\n[note] Stopped at the seconds limit.\n")],
                  None, "I couldn't produce an answer.") == [("final_answer", "The first half")]
    # a relay that stopped says why after the text it sent, in the answer's place
    why = "The write_file call reached the token limit twice before its arguments were complete, so I stopped."
    assert stream(tmp_path, monkeypatch, [("content", "I'll write it.")], None, why, stopped=True) == [
        ("commentary", "I'll write it."), ("final_answer", why)]


def test_lobes_own_tool_steps_between_replies_are_shown_in_order(tmp_path, monkeypatch):
    deltas = [("content", "Let me compute."), ("step", "\n[python] {}\n391\n"), ("reasoning", "so"), ("content", "391 parts."),
              ("step", "\n[note] n\n")]
    assert stream(tmp_path, monkeypatch, deltas, "391 parts.", "391 parts.") == [
        ("commentary", "Let me compute."), ("reasoning", "[python] {}\n391"), ("commentary", "> [python] {}\n> 391\n> so"),
        ("final_answer", "391 parts.")]


def test_only_the_quoted_thinking_is_dropped_from_the_history():
    from lobes.responses import _messages
    said = lambda text: {"type": "message", "role": "assistant", "phase": "commentary", "content": [{"type": "output_text", "text": text}]}
    assert [m["content"] for m in _messages({"input": [said("> thinking"), said("Found it: x is None.")]})] == \
        [[{"type": "text", "text": "Found it: x is None."}]]


def test_the_same_command_gives_the_same_result_text():
    from lobes.responses import _messages
    run = lambda chunk, secs: {"type": "function_call_output", "call_id": "c", "output":
                               f"Chunk ID: {chunk}\nWall time: {secs} seconds\nProcess exited with code 0\nOutput:\nWall time: 3 seconds\n"}
    first, again = (_messages({"input": [run(*a)]})[0]["content"] for a in (("6a1982", "0.2349"), ("961fd9", "0.2306")))
    assert first == again == "Process exited with code 0\nOutput:\nWall time: 3 seconds\n"
    script = [{"type": "input_text", "text": "Script failed\nWall time 0.4 seconds\nOutput:\nboom"}]
    assert _messages({"input": [{"type": "function_call_output", "call_id": "c", "output": script}]})[0]["content"] == "Script failed\nOutput:\nboom"
    assert _messages({"input": [{"type": "function_call_output", "call_id": "c", "output": "Wall time: 1 seconds"}]})[0]["content"] == "Wall time: 1 seconds"


def test_the_same_command_while_it_still_runs_waits_for_it():
    from lobes.responses import _call, _running
    search = '{"cmd": "python ida.py search_text", "shell": "powershell"}'
    names = {"exec_command": (None, "exec_command"), "write_stdin": (None, "write_stdin")}
    step = lambda i, name, arguments, out: [{"type": "function_call", "call_id": f"c{i}", "name": name, "arguments": arguments},
                                            {"type": "function_call_output", "call_id": f"c{i}", "output": out}]
    started = step(1, "exec_command", search, "Chunk ID: 560eb8\nWall time: 10.0112 seconds\nProcess running with session ID 85958\nOutput:\n")
    again = {"id": "c9", "function": {"name": "exec_command", "arguments": search}}
    waited = _call(again, names, _running(started))
    assert waited["name"] == "write_stdin" and json.loads(waited["arguments"]) == {"session_id": 85958, "yield_time_ms": 30000}
    done = started + step(2, "write_stdin", waited["arguments"], "Wall time: 3.1 seconds\nProcess exited with code 0\nOutput:\nfound\n")
    assert _call(again, names, _running(done))["name"] == "exec_command"
    assert _call(again, {"exec_command": (None, "exec_command")}, _running(started))["name"] == "exec_command"


def test_a_turn_whose_only_tool_is_hosted_keeps_the_local_tools():
    from lobes.responses import _tools
    flat, _ = _tools([{"type": "web_search", "external_web_access": False}])   # the client expects us to run this one
    assert flat == []
    assert runner.TaskState("t", "who won", [], client_tools=flat).client_tools is None
    kept = [{"type": "function", "function": {"name": "exec_command"}}]
    assert runner.TaskState("t", "ls", [], client_tools=kept).client_tools == kept


def test_escalation_wording_only_goes_out_with_an_escalation():
    from lobes.responses import _call
    out = lambda arguments: _call({"id": "c", "function": {"name": "exec_command", "arguments": arguments}}, {})["arguments"]
    assert json.loads(out('{"cmd": "ls", "justification": "why", "prefix_rule": ["ls"]}')) == {"cmd": "ls"}
    escalated = '{"cmd": "ls", "justification": "why", "sandbox_permissions": "require_escalated"}'
    assert out(escalated) == escalated and out('{"cmd": "ls"}') == '{"cmd": "ls"}' and out("not json") == "not json"
