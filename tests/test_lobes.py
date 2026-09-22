"""Offline contracts for request limits, tools, the relay and the eval harness."""
import json

import httpx
import pytest

from lobes import config, models, providers, runner, server, task, tools
from lobes.providers import Reply
from lobes.lobe import motor
from lobes.task import TaskState, Turn


def reply(data=None, tokens=10, finish="stop", text=None, calls=()):
    return Reply(json.dumps(data) if text is None else text, data, None,
                 {"prompt_tokens": 20, "completion_tokens": tokens, "total_tokens": tokens + 20}, 1, {}, finish,
                 tool_calls=[{"id": f"c{i}", "type": "function",
                              "function": {"name": name, "arguments": args if isinstance(args, str) else json.dumps(args)}}
                             for i, (name, args) in enumerate(calls)])


def says(text):
    return reply(text=text)


def calls(*pairs):
    return reply(text="", calls=pairs)


def rewrites(**relay):
    """Lists the review twice, so a rejection is rewritten. The shipped relay lists it once and notes it."""
    return {"specialists": {"checks": {"medium": ["language", "language"]}, **relay}}


@pytest.fixture
def simulate(tmp_path, monkeypatch):
    cfg = config.load()
    cfg["_root"] = tmp_path
    cfg["seed"] = 7
    seen = []
    monkeypatch.setattr(models.ModelManager, "validate", lambda self, names: {})
    monkeypatch.setattr(models.ModelManager, "ensure", lambda self, name: None)

    def run(goal, replies, **overrides):
        kw = {k: overrides.pop(k) for k in ("images", "messages", "client_tools") if k in overrides}
        cfg.update(overrides)
        responses = {"executive": ["hard"], **replies}
        counts = {}
        by_model = {spec.split("/", 1)[1]: name for name, spec in cfg["profiles"][cfg["profile"]].items() if "/" in spec}

        def chat(provider, model, messages, **kwargs):
            lobe = by_model[model]
            seen.append((lobe, [dict(m) for m in messages], kwargs))
            choices = responses[lobe]
            index = counts.get(lobe, 0)
            counts[lobe] = index + 1
            result = choices[min(index, len(choices) - 1)]
            if isinstance(result, Exception):
                raise result
            if isinstance(result, str) and kwargs.get("choices"):
                return reply(result if result in kwargs["choices"] else None, text=result)
            return result if isinstance(result, Reply) else says(result) if isinstance(result, str) else reply(result)

        monkeypatch.setattr(providers, "chat", chat)
        state = runner.run(cfg, goal, **kw)
        trace = [json.loads(line) for line in (tmp_path / "runs" / state.task_id / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
        return state, seen, trace
    return run


def test_model_written_code_runs_with_root_dropped(tmp_path):
    # a test prompt once got the python tool to power off the eval box
    import os
    drop = tools._drop()
    if os.name != "posix" or os.geteuid() != 0:
        assert drop == {}                       # nothing to drop, and asking costs nothing
    else:
        import pwd
        who = pwd.getpwnam(tools.UNPRIVILEGED)
        assert drop == {"user": who.pw_uid, "group": who.pw_gid, "extra_groups": []}
        assert drop["group"] != 0               # root's group reads a 0701 home directory as the group class
    assert tools.run("python", {"code": "print(6*7)"}, tmp_path)["stdout"].strip() == "42"   # and it still runs


def test_tools_timeout_and_file_boundary(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "TIMEOUT", 1)
    slow = tools.run("python", {"code": "import time\nprint('started', flush=True)\ntime.sleep(5)"}, tmp_path)
    assert slow["exit"] == -1 and isinstance(slow["stdout"], str)
    json.dumps(slow)
    assert tools.run("python", {"code": "print('a\\nb')"}, tmp_path)["stdout"] == "a\nb\n"
    assert tools.run("python", {"code": "s = 'a\\nb'\nprint(len(s))"}, tmp_path)["stdout"].strip() == "3"
    assert tools.run("python", {"code": "x = 6\nx, x * 7"}, tmp_path)["stdout"].strip() == "(6, 42)"    # came back empty
    assert tools.run("python", {"code": "data = (\n    'a',\n    'b',\n)"}, tmp_path)["stdout"] == ""     # was echoed as ()
    assert tools.run("web_fetch", {"url": "http://localhost:PORT/x"}, tmp_path)["exit"] == 1     # crashed the request
    assert tools.run("write_file", {"path": "../outside", "content": "x"}, tmp_path)["exit"] != 0
    assert tools.run("no_such_tool", {}, tmp_path)["exit"] != 0
    assert tools.run("edit_file", {"path": ".", "old": "a", "new": "b"}, tmp_path)["exit"] == 1     # crashed the request
    assert tools.run("write_file", {"path": ".", "content": "x"}, tmp_path)["exit"] == 1


def test_repo_suite_computes_its_answers_from_the_package(tmp_path):
    from lobes.hard import repo
    pkg = tmp_path / "repo"                     # already there, so corpus() keeps it instead of copying the real one
    (pkg / "sub").mkdir(parents=True)
    (pkg / "alpha.py").write_text("LIMIT = 7\n\ndef seeker(one, two, tail):\n    return tail\n", encoding="utf-8")
    (pkg / "sub" / "beta.py").write_text("def other():\n    return seeker(1, 2, 3)\n", encoding="utf-8")
    gold = {it["id"]: it["gold"] for it in repo.load(tmp_path)}
    assert gold["repo-where-seeker"] == "alpha.py"
    assert gold["repo-value-LIMIT"] == "7"
    assert gold["repo-calls-seeker"] == "beta.py"        # the file calling it, not the one defining it
    assert gold["repo-param-seeker"] == "tail"
    assert "repo-where-other" in gold and "repo-param-other" not in gold      # too few parameters to ask about

    (pkg / "sub" / "alpha.py").write_text("", encoding="utf-8")
    with pytest.raises(ValueError):             # a shared file name would make every where answer ambiguous
        repo.load(tmp_path)


def test_web_search_reads_the_url_out_of_the_redirect(tmp_path, monkeypatch):
    # every duckduckgo result url arrives percent-encoded in the uddg parameter of its own redirect
    page = ('<div class="result"><h2><a rel="nofollow" class="result__a"'
            ' href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa%3Fb%3D1&amp;rut=ff">An &amp; example</a></h2>'
            '<a class="result__snippet" href="//duckduckgo.com/l/?uddg=x">The <b>snippet</b>.</a></div>'
            '<div class="result"><a class="result__a" href="https://example.org/">No redirect</a></div>')
    monkeypatch.setattr(tools.httpx, "get", lambda url, **kw: httpx.Response(
        200, request=httpx.Request("GET", url), text=page))
    found = tools.run("web_search", {"query": "an example"}, tmp_path)
    assert found["exit"] == 0
    assert found["content"].split("\n\n") == ["An & example\nhttps://example.com/a?b=1\nThe snippet .",
                                              "No redirect\nhttps://example.org/"]

    monkeypatch.setattr(tools.httpx, "get", lambda url, **kw: httpx.Response(
        200, request=httpx.Request("GET", url), text="<html>no results</html>"))
    assert tools.run("web_search", {"query": "x"}, tmp_path)["exit"] == 1       # the markup moved, and it says so


def test_simple_request_uses_tools_without_thinking_or_review(simulate):
    state, seen, trace = simulate("what time is it", {
        "executive": ["easy"],
        "reasoning": [calls(("python", {"code": "print('12:00')"})), "It is 12:00."]})
    assert state.answer == "It is 12:00." and state.turn.route == "simple"
    assert [lobe for lobe, *_ in seen] == ["executive", "reasoning", "reasoning"]
    assert [kw["thinking_budget"] for *_, kw in seen] == [None, task.SIMPLE_THINK, task.SIMPLE_THINK]
    assert seen[2][1][1]["tool_calls"][0]["function"]["name"] == "python"
    assert seen[2][1][-1] == {"role": "tool", "tool_call_id": "c0", "content": "12:00"}
    assert {t["function"]["name"] for t in seen[1][2]["tools"]} == {"python", "motor"}


def test_hard_request_thinks_at_its_level_and_a_passing_review_ships_the_draft(simulate):
    state, seen, _ = simulate("A box holds 23 parts. How many parts are in 17 boxes?", {
        "reasoning": [calls(("python", {"code": "print(17 * 23)"})), "17 boxes hold 391 parts."],
        "language": ["OK"]}, effort="low")
    assert state.answer == "17 boxes hold 391 parts." and not state.turn.problems
    assert [kw["thinking_budget"] for lobe, _, kw in seen if lobe == "reasoning"] == [1024, 1024]
    lobe, messages, kw = seen[-1]
    assert lobe == "language" and kw["tools"] is None
    work = messages[-1]["content"]
    assert "print(17 * 23)" in work and "391" in work and "17 boxes hold 391 parts." in work
    assert "Result" in work.split("print(17 * 23)")[1]


def test_send_back_repairs_once_in_the_same_conversation(simulate):
    state, seen, trace = simulate("What is 2 to the power 10?", {
        "reasoning": ["2^10 = 1000", "2^10 = 1024"],
        "language": ["2^10 is 1024, not 1000", "OK"]}, relays=rewrites())
    assert state.answer == "2^10 = 1024" and state.turn.problems == ["2^10 is 1024, not 1000"]
    repair = [messages for lobe, messages, _ in seen if lobe == "reasoning"][-1]
    assert repair[1] == {"role": "assistant", "content": "2^10 = 1000"}
    assert repair[-1]["role"] == "user" and "1024, not 1000" in repair[-1]["content"]
    assert not state.uncertainties


def test_the_shipped_relay_ships_the_draft_a_review_rejected_and_notes_why(simulate):
    state, seen, trace = simulate("What is 2 to the power 10?", {
        "reasoning": ["2^10 = 1000"], "language": ["2^10 is 1024, not 1000"]})
    assert state.answer == "2^10 = 1000" and state.turn.problems == ["2^10 is 1024, not 1000"]
    assert state.uncertainties == ["A review still found a problem: 2^10 is 1024, not 1000"]
    assert [lobe for lobe, *_ in seen] == ["executive", "reasoning", "language"]     # the expert is not asked again
    assert [(r["by"], r["ok"]) for r in trace if r["kind"] == "review"] == [("language", False)]


def test_the_review_model_can_read_the_request_in_front_of_the_draft_instead(simulate):
    # wrong answers here mostly missed one condition out of several
    state, seen, trace = simulate("Write a resume with no commas.", {
        "reasoning": ["Here it is."], "language": [{"requirements": ["a resume", "no commas anywhere"]}]},
        relays={"specialists": {"language": "requirements"}})
    assert [lobe for lobe, *_ in seen] == ["executive", "language", "reasoning"]     # asked first, and only once
    assert ("What another lobe read the request as asking of the reply:\n"
            "- a resume\n- no commas anywhere") in seen[2][1][0]["content"]
    assert state.answer == "Here it is." and not state.turn.problems      # nothing reviews the draft, so nothing rejects it
    assert [r["text"] for r in trace if r["kind"] == "requirements"] == ["- a resume\n- no commas anywhere"]
    # the simple route leaves it out, the same as the review it replaces
    mark = len(seen)
    _, seen, _ = simulate("hi", {"executive": ["easy"], "reasoning": ["hello"]},
                          relays={"specialists": {"language": "requirements"}})
    assert [lobe for lobe, *_ in seen[mark:]] == ["executive", "reasoning"]


def test_the_reviewer_that_sent_a_draft_back_does_not_read_the_repair_again(simulate):
    # its second rejection could only add a note; the repaired draft shipped either way
    state, seen, _ = simulate("What is 2 to the power 10?", {
        "reasoning": ["1000", "1024"], "language": ["wrong"]}, relays=rewrites())
    assert state.answer == "1024" and state.turn.problems == ["wrong"] and not state.uncertainties
    assert [lobe for lobe, *_ in seen] == ["executive", "reasoning", "language", "reasoning"]
    # a different last checker still reads the repair (the self-check is the expert's model, so its reply is in reasoning's list)
    state, seen, _ = simulate("What is 2 to the power 10?", {"reasoning": ["1000", "wrong", "1024"], "language": ["still wrong"]},
                              relays={"specialists": {"checks": {"medium": ["check", "language"]}}})
    assert state.answer == "1024" and state.turn.problems == ["wrong", "still wrong"]
    assert state.uncertainties == ["A review still found a problem: still wrong"]


def test_a_reply_that_ends_inside_its_thinking_is_asked_again_to_write_it_out(simulate):
    ended = Reply("", None, "The OS is Windows 10, so the answer is: Windows 10.", {"total_tokens": 5}, 1, {})
    state, seen, _ = simulate("which OS is this", {"executive": ["easy"], "reasoning": [ended, "Windows 10."]})
    assert state.answer == "Windows 10."
    asked = [(msgs, kw) for lobe, msgs, kw in seen if lobe == "reasoning"]
    assert [kw["thinking"] for _, kw in asked] == [True, False]
    assert asked[1][0][-1] == {"role": "assistant", "content": "", "reasoning_content": ended.reasoning}
    # an empty re-ask used to leave Codex with no answer at all
    state, _, _ = simulate("which OS is this", {"executive": ["easy"], "reasoning": [ended, ""]})
    assert state.answer == ended.reasoning


def test_a_body_cut_off_at_the_token_limit_is_asked_for_its_conclusion(simulate):
    # the work got written but not the last line, so there was nothing to grade
    half = reply(text="Star A sits at declination -30, so from Paranal it", finish="length")
    state, seen, _ = simulate("which stars are visible",
                              {"executive": ["easy"], "reasoning": [half, r"So the answer is \boxed{C}."]})
    assert state.answer == "Star A sits at declination -30, so from Paranal it\n\n" + r"So the answer is \boxed{C}."
    asked = [(msgs, kw) for lobe, msgs, kw in seen if lobe == "reasoning"]
    assert [kw["thinking"] for _, kw in asked] == [True, False]      # the conclusion is stated, not thought about
    assert asked[1][0][-2] == {"role": "assistant", "content": half.text}
    assert asked[1][1]["max_tokens"] == task.CONCLUSION
    # nothing to add: the draft stays instead of being blanked
    state, _, _ = simulate("which stars are visible", {"executive": ["easy"], "reasoning": [half, ""]})
    assert state.answer == half.text


def test_a_conclusion_that_is_itself_cut_off_is_dropped(simulate):
    # readers take the last block as the answer, so a cut conclusion would bury whatever the draft reached
    half = reply(text="Star A sits at declination -30, so from Paranal it", finish="length")
    again = reply(text="To determine which stars are visible we check two conditions. First,", finish="length")
    state, seen, _ = simulate("which stars are visible",
                              {"executive": ["easy"], "reasoning": [half, again, r"\boxed{C}"]})
    assert state.answer == half.text        # the draft ships alone; the cut conclusion is not appended
    assert [lobe for lobe, _, _ in seen if lobe == "reasoning"] == ["reasoning"] * 2      # and is not asked again


def test_a_call_lobes_runs_that_repeats_with_the_same_result_thinks_then_stops_with_the_result(simulate):
    again = calls(("python", {"code": "x = 1"}))
    state, seen, trace = simulate("what is x", {"executive": ["easy"], "reasoning": [again, again, again, "x is 1"]},
                                  relays={"specialists": {"think": "off"}})
    assert state.answer.startswith("python returned the same result three times")
    asked = [kw for lobe, _, kw in seen if lobe == "reasoning"]
    assert [kw["thinking_budget"] for kw in asked] == [None, None, task.EFFORT["medium"]["think"]]
    assert [r["tool"] for r in trace if r["kind"] == "stalled"] == ["python"] and not state.spend.capped and state.stopped
    # two snippets sent in turn, each failing the same way. the call cap used to end this one
    n, other = len(seen), calls(("python", {"code": "y = 2"}))
    state, seen, trace = simulate("what is x", {"executive": ["easy"], "reasoning": [again, other, again, other, again, "x is 1"]},
                                  relays={"specialists": {"think": "off"}})
    think = task.EFFORT["medium"]["think"]
    assert [kw["thinking_budget"] for lobe, _, kw in seen[n:] if lobe == "reasoning"] == [None, None, None, think, think]
    assert len([r for r in trace if r["kind"] == "stalled"]) == 2 and state.stopped and not state.spend.capped


def test_a_call_cut_off_by_the_token_limit_runs_nothing_and_is_retried_with_thinking(simulate):
    cut = reply(text="I'll write it.", finish="length", calls=[("write_file", '{"path": "a.txt", "content": "3486784401348')])
    state, seen, trace = simulate("write 3**500 to a.txt", {"executive": ["easy"],
                                  "reasoning": [cut, calls(("python", {"code": "print(1)"})), "done"]}, relays={"specialists": {"think": "off"}})
    asked = [(msgs, kw) for lobe, msgs, kw in seen if lobe == "reasoning"]
    think, answer = task.EFFORT["medium"]["think"], task.ANSWER
    assert [kw["thinking_budget"] for _, kw in asked] == [None] + [think] * 2    # like a stall, it stays up
    assert [kw["max_tokens"] for _, kw in asked] == [answer, think + 2 * answer, think + answer]    # the retry has room for a long call
    assert "token limit" in asked[1][0][-1]["content"] and asked[1][0][-1]["role"] == "tool"
    assert state.answer == "done" and [r["tools"] for r in trace if r["kind"] == "cut"] == [["write_file"]]
    bash = {"type": "function", "function": {"name": "bash", "parameters": {"type": "object"}}}
    state, seen, _ = simulate("write 3**500 to a.txt", {"executive": ["easy"], "reasoning": [cut, calls(("bash", {"command": "ls"}))]},
                              relays={"specialists": {"think": "off"}}, client_tools=[bash], messages=[{"role": "user", "content": "write 3**500 to a.txt"}])
    assert [c["function"]["name"] for c in state.tool_calls] == ["bash"]     # the client never gets the broken call
    # cut again, the call does not fit: the request stops and says why instead of retrying until a cap
    n = len(seen)
    state, seen, trace = simulate("write 3**500 to a.txt", {"executive": ["easy"], "reasoning": [cut, cut, "inline"]},
                                  client_tools=[bash], messages=[{"role": "user", "content": "write 3**500 to a.txt"}])
    assert len([lobe for lobe, *_ in seen[n:] if lobe == "reasoning"]) == 2 and not state.tool_calls and not state.spend.capped
    assert state.answer.startswith("The write_file call reached the token limit twice") and state.stopped
    assert len([r for r in trace if r["kind"] == "cut"]) == 2
    # a retry that ends inside its thinking is asked again with the same doubled room
    ended = Reply("", None, "It is long.", {"total_tokens": 5}, 1, {})
    n = len(seen)
    simulate("write 3**500 to a.txt", {"executive": ["easy"], "reasoning": [cut, ended, cut]},
             client_tools=[bash], messages=[{"role": "user", "content": "write 3**500 to a.txt"}])
    assert [kw["max_tokens"] for lobe, _, kw in seen[n:] if lobe == "reasoning"][2] == 2 * answer
    # a rewrite that stops keeps the draft the review sent back, with both reasons as notes
    state, _, _ = simulate("write 3**500 to a.txt", {"reasoning": ["Here it is.", cut, cut], "language": ["wrong"]},
                           relays=rewrites(think="off"))
    assert state.answer == "Here it is." and not state.stopped
    assert state.uncertainties == ["A review still found a problem: wrong",
                                   "The write_file call reached the token limit twice before its arguments were complete, so I stopped."]


def test_think_first_thinks_on_the_first_call_of_a_draft_and_of_a_rewrite(simulate):
    step = calls(("python", {"code": "print(17 * 23)"}))
    state, seen, _ = simulate("How many parts in 17 boxes of 23?", {
        "reasoning": [step, "381 parts", calls(("python", {"code": "print(23 * 17)"})), "391 parts"], "language": ["17 x 23 is 391."]},
        relays=rewrites(think="first"))
    think = task.EFFORT["medium"]["think"]
    assert state.answer == "391 parts"
    assert [kw["thinking_budget"] for lobe, _, kw in seen if lobe == "reasoning"] == [think, None, think, None]
    bash = {"type": "function", "function": {"name": "bash", "parameters": {"type": "object"}}}
    ask = [{"role": "user", "content": "list the files"}]
    done = [{"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "bash", "arguments": '{"command": "ls"}'}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "a.txt"}]
    for messages, budget in ((ask, think), (ask + done, None)):
        _, seen, _ = simulate("list the files", {"reasoning": [step]}, relays={"specialists": {"think": "first"}},
                              client_tools=[bash], messages=messages)
        assert seen[-1][2]["thinking_budget"] == budget
    # a client's tool result: the step goes without thinking, a rewrite after a review thinks
    n = len(seen)
    state, seen, _ = simulate("list the files", {"reasoning": ["381 parts", "391 parts"], "language": ["wrong"]},
                              relays=rewrites(think="first"), client_tools=[bash], messages=ask + done)
    assert state.answer == "391 parts"
    assert [kw["thinking_budget"] for lobe, _, kw in seen[n:] if lobe == "reasoning"] == [None, think]
    # the same call twice with the same result: the next step thinks even in a continuation
    again = [{**done[0], "tool_calls": [{**done[0]["tool_calls"][0], "id": "c2"}]}, {**done[1], "tool_call_id": "c2"}]
    _, seen, _ = simulate("list the files", {"executive": ["easy"], "reasoning": ["a.txt"]}, relays={"specialists": {"think": "first"}},
                          client_tools=[bash], messages=ask + done + again)
    assert seen[-1][2]["thinking_budget"] == think


def test_motor_answers_the_request_and_keeps_the_results(simulate):
    state, seen, _ = simulate("say hi from the shell", {
        "executive": ["easy"],
        "reasoning": [calls(("motor", {"request": "run echo hi in the shell"})), "The shell said hi."],
        "motor": [calls(("shell", {"command": "echo hi"})), "The shell printed hi."]})
    assert state.answer == "The shell said hi."
    assert [lobe for lobe, *_ in seen] == ["executive", "reasoning", "motor", "motor", "reasoning"]
    assert seen[2][1][-1]["content"] == "run echo hi in the shell"
    assert [t["function"]["name"] for t in seen[2][2]["tools"]] == motor.HAND     # python stays with reasoning
    assert seen[3][2]["tools"] is None          # the answering turn runs no tool: another step goes through reasoning
    given = seen[-1][1][-1]                     # what reasoning is handed: the hand's words, not the shell's output
    assert given["role"] == "tool" and given["content"] == "The shell printed hi."

    simulate("say hi from the shell", {
        "executive": ["easy"],
        "reasoning": [calls(("motor", {"request": "run echo hi in the shell"})), "ok"],
        "motor": [calls(("shell", {"command": "echo hi"})), ""]})
    raw = seen[-1][1][-1]                       # it ran the tools and said nothing: the results are all there is
    assert raw["content"].startswith("shell ") and raw["content"].endswith("hi")


def test_without_a_tool_hand_reasoning_holds_every_tool(simulate):
    state, seen, _ = simulate("hello", {"reasoning": ["hi"]}, profile="bare-9b")
    assert state.answer == "hi" and [lobe for lobe, *_ in seen] == ["reasoning"]
    assert [t["function"]["name"] for t in seen[0][2]["tools"]] == list(tools.TOOLS)
    assert seen[0][2]["thinking_budget"] == task.EFFORT["medium"]["think"]


def test_bad_tool_calls_are_answered_not_run(simulate, monkeypatch):
    monkeypatch.setattr(tools, "run", lambda *a, **k: pytest.fail("must not run"))
    bad = reply(text="", calls=[("shell", {"command": "echo hi"}), ("python", "print(1")])
    _, seen, _ = simulate("x", {"reasoning": [bad, "ok"], "language": ["ok"]})
    assert [m["content"] for m in seen[2][1][-2:]] == ["There is no tool named shell.", "The arguments were not a JSON object."]


def test_a_relay_without_thinking_checks_every_answer_in_order(simulate):
    relay = {"think": "off", "checks_on": "all", "recheck": True,
             "checks": {"medium": ["check", "check", "language"]}}
    state, seen, trace = simulate("How many parts in 17 boxes of 23?", {
        "executive": ["easy"], "reasoning": ["381 parts", "OK", "17 x 23 is 391, not 381.", "391 parts"],
        "language": ["OK"]}, relays={"specialists": relay})
    assert state.answer == "391 parts" and state.turn.problems == ["17 x 23 is 391, not 381."]
    assert [lobe for lobe, *_ in seen] == ["executive"] + ["reasoning"] * 4 + ["language"]
    assert not any(kw["thinking"] for *_, kw in seen)
    assert [r["by"] for r in trace if r["kind"] == "review"] == ["check", "check", "language"]


def test_an_escalating_relay_thinks_only_on_the_rewrite(simulate):
    state, seen, _ = simulate("How many parts in 17 boxes of 23?", {
        "reasoning": ["381 parts", "391 parts"], "language": ["17 x 23 is 391.", "OK"]},
        relays=rewrites(think="escalate"))
    assert state.answer == "391 parts"
    assert [kw["thinking_budget"] for lobe, _, kw in seen if lobe == "reasoning"] == [None, task.EFFORT["medium"]["think"]]


def test_an_expert_model_can_set_its_own_think_mode(simulate):
    cfg = config.load()
    expert = cfg["profiles"][cfg["profile"]]["reasoning"].split("/", 1)[1]
    models = {**cfg["models"], expert: {**cfg["models"][expert], "think": "escalate"}}
    state, seen, _ = simulate("How many parts in 17 boxes of 23?", {
        "reasoning": ["381 parts", "391 parts"], "language": ["17 x 23 is 391.", "OK"]}, models=models, relays=rewrites())
    assert state.answer == "391 parts"
    assert [kw["thinking_budget"] for lobe, _, kw in seen if lobe == "reasoning"] == [None, task.EFFORT["medium"]["think"]]


def test_a_client_call_repeated_with_the_same_result_is_read_with_thinking(simulate):
    bash = {"type": "function", "function": {"name": "bash", "parameters": {"type": "object"}}}
    step = lambda n, out: [{"role": "assistant", "content": "", "tool_calls": [
        {"id": f"c{n}", "type": "function", "function": {"name": "bash", "arguments": '{"command": "ls", "justification": "x"}'}}]},
        {"role": "tool", "tool_call_id": f"c{n}", "content": out}]
    ask = [{"role": "user", "content": "list the files"}]
    off, refused = {"specialists": {"think": "off"}}, "justification requires sandbox_permissions"
    _, seen, trace = simulate("list the files", {"executive": ["easy"], "reasoning": ["a.txt"]}, relays=off,
                             client_tools=[bash], messages=ask + step(1, refused) + step(2, "a.txt"))
    assert seen[-1][2]["thinking_budget"] is None and not any(r["kind"] == "stalled" for r in trace)
    _, seen, trace = simulate("list the files", {"executive": ["easy"], "reasoning": ["a.txt"]}, relays=off,
                             client_tools=[bash], messages=ask + step(1, refused) + step(2, refused))
    assert seen[-1][2]["thinking_budget"] == task.EFFORT["medium"]["think"] and [r["tool"] for r in trace if r["kind"] == "stalled"] == ["bash"]
    before = len(seen)      # simulate keeps every run's calls in one list
    state, seen, trace = simulate("list the files", {"executive": ["easy"], "reasoning": ["a.txt"]}, relays=off, client_tools=[bash],
                                  messages=ask + step(1, refused) + step(2, refused) + step(3, refused))
    assert refused in state.answer and not state.tool_calls and "reasoning" not in [lobe for lobe, *_ in seen[before:]]
    assert any(r["kind"] == "stop" for r in trace) and not any(r["kind"] == "stalled" for r in trace)


@pytest.mark.parametrize("error", [
    httpx.HTTPStatusError("500", request=httpx.Request("POST", "http://test"), response=httpx.Response(500)),
    RuntimeError("gemma4-e2b failed to load")])
def test_review_server_error_keeps_the_draft(simulate, error):
    state, _, trace = simulate("How many parts in 17 boxes of 23?", {"reasoning": ["391 parts"], "language": [error]})
    assert state.answer == "391 parts"
    assert any(record["kind"] == "language_error" for record in trace)


def test_each_call_gets_its_own_seed(simulate):
    _, seen, _ = simulate("x", {"reasoning": [calls(("python", {"code": "print(1)"})), "1"], "language": ["OK"]})
    assert [kw["seed"] for *_, kw in seen] == [7, 8, 9, 10]


def test_call_cap_ends_a_tool_loop_with_the_answer_it_has(simulate, monkeypatch):
    monkeypatch.setitem(task.EFFORT["medium"], "calls", 3)
    loop = calls(("python", {"code": "print(1)"}))
    state, seen, _ = simulate("loop", {"reasoning": [loop, loop, "1, from the run above."]})
    # the cap stops the tool loop, and then the request still owes an answer: one more call, no tools
    assert len(seen) == 4 and state.spend.capped == "calls" and state.answer == "1, from the run above."
    assert state.uncertainties == ["Stopped at the request's calls limit."]
    assert seen[-1][2]["tools"] is None and seen[-1][2]["max_tokens"] == task.CONCLUSION
    assert seen[-1][1][-1]["role"] == "user"

    state, _, _ = simulate("loop", {"reasoning": [loop, loop, RuntimeError("the server is gone")]})
    assert state.answer == "I couldn't produce an answer."     # only when there is no last word to be had


def test_effort_names():
    assert task.effort({"effort": "max"}) is task.EFFORT["high"]
    with pytest.raises(ValueError):
        task.effort({"effort": "ultra"})


def test_vision_passes_labelled_observations(simulate, monkeypatch, tmp_path):
    from lobes.lobe import perception
    monkeypatch.setattr(perception, "ocr", lambda image: ["Total 42"])
    state, seen, _ = simulate("Read the total", {
        "perception": [{"description": "receipt", "text": "Total 42", "details": []}],
        "reasoning": ["42"], "language": ["OK"]}, images=[tmp_path / "receipt.png"])
    assert state.answer == "42"
    assert [lobe for lobe, *_ in seen] == ["perception", "reasoning", "language"]
    assert "lobe:perception" in seen[1][1][0]["content"] and "tool:ocr" in seen[1][1][0]["content"]


def test_generation_limit_is_enforced_before_each_call(tmp_path, monkeypatch):
    cfg = config.load()
    ctx = runner.Ctx(cfg, "specialists", runner.Trace(tmp_path / "trace"), tmp_path)
    state = TaskState("t", "request", [])
    state.spend.usage["completion_tokens"] = ctx.effort["tokens"] - 7
    monkeypatch.setattr(ctx.mm, "ensure", lambda name: None)
    sent = []
    monkeypatch.setattr(providers, "chat", lambda *args, **kw: (sent.append(kw), reply({}, 7))[1])
    ctx.chat(state, "reasoning", [], max_tokens=9000)
    assert sent[0]["max_tokens"] == 7
    monkeypatch.setattr(ctx.mm, "ensure", lambda name: pytest.fail("exhausted request must not load"))
    with pytest.raises(task.BudgetExceeded):
        ctx.chat(state, "language", [], max_tokens=100)
    assert len(sent) == 1 and state.spend.capped == "tokens"


def test_timeout_does_not_start_a_second_generation(tmp_path, monkeypatch):
    ctx = runner.Ctx(config.load(), "specialists", runner.Trace(tmp_path / "trace"), tmp_path)
    monkeypatch.setattr(ctx.mm, "ensure", lambda name: None)
    sent = []
    def fail(*args, **kw):
        sent.append(kw)
        raise httpx.ReadTimeout("deadline")
    monkeypatch.setattr(providers, "chat", fail)
    state = TaskState("t", "request", [])
    with pytest.raises(task.BudgetExceeded):
        ctx.chat(state, "reasoning", [], thinking=1024)
    assert len(sent) == 1 and state.spend.capped == "seconds"


def sse(text):
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: {")]


@pytest.mark.parametrize("stream", [False, True])
def test_api_exposes_uncertainty_separately_from_code(tmp_path, monkeypatch, stream):
    from starlette.testclient import TestClient
    from lobes import api
    state = TaskState("t", "write code", [])
    state.answer = "def add(a, b):\n    return a + b"
    state.uncertainties = ["Source has not been executed."]
    monkeypatch.setattr(api, "run", lambda *a, **kw: state)
    with TestClient(server.make_app(dict(config.load(), _root=tmp_path))) as client:
        result = client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "write code"}], "stream": stream})
    if stream:
        chunks = sse(result.text)
        assert "".join(c["choices"][0]["delta"].get("content", "") for c in chunks if c["choices"]) == state.answer
        assert chunks[-1]["lobes"]["uncertainties"] == state.uncertainties
    else:
        assert result.json()["choices"][0]["message"]["content"] == state.answer
        assert result.json()["lobes"]["uncertainties"] == state.uncertainties


def test_api_streams_two_replies_apart_and_a_stop_reason_after_them(tmp_path, monkeypatch):
    from starlette.testclient import TestClient
    from lobes import api
    state = TaskState("t", "write it", [])
    state.answer = "The write_file call reached the token limit twice before its arguments were complete, so I stopped."
    state.stopped = True

    def run(*a, on_delta=None, **kw):
        for kind, text in (("content", "I'll write it."), ("step", "\n[retry] a tool call was cut off at the token limit\n"),
                           ("content", "Writing it.")):
            on_delta(kind, text)
        return state
    monkeypatch.setattr(api, "run", run)
    with TestClient(server.make_app(dict(config.load(), _root=tmp_path))) as client:
        chunks = sse(client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "write it"}], "stream": True}).text)
    content = "".join(c["choices"][0]["delta"].get("content", "") for c in chunks if c["choices"])
    assert content == "I'll write it.\n\nWriting it.\n\n" + state.answer


@pytest.mark.parametrize("stream", [False, True])
def test_api_passes_a_server_error_on_so_a_client_can_compact(tmp_path, monkeypatch, stream):
    from starlette.testclient import TestClient
    from lobes import api
    full = httpx.Response(400, request=httpx.Request("POST", "http://test"),
                          json={"error": {"message": "the request exceeds the available context size, try increasing it"}})

    def run(*a, **kw):
        raise httpx.HTTPStatusError("400", request=full.request, response=full)
    monkeypatch.setattr(api, "run", run)
    with TestClient(server.make_app(dict(config.load(), _root=tmp_path))) as client:
        result = client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "hi"}], "stream": stream})
    error = sse(result.text)[-1]["error"] if stream else result.json()["error"]
    assert result.status_code == (200 if stream else 400)
    assert error["code"] == "context_length_exceeded" and "exceeds the available context size" in error["message"]
    busy = httpx.Response(500, request=full.request, json={"error": {"message": "Context size has been exceeded."}})
    assert api.failure(httpx.HTTPStatusError("500", request=full.request, response=busy)) == (
        500, {"message": "Context size has been exceeded.", "type": "server_error", "code": None})


def test_provider_stream_assembles_the_same_reply(monkeypatch):
    import contextlib
    lines = ['data: {"choices":[{"delta":{"reasoning_content":"think"}}]}',
             'data: {"choices":[{"delta":{"content":"Let me run it."}}]}',
             'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c0","function":{"name":"bash","arguments":"{\\"com"}}]}}]}',
             'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"mand\\": \\"ls\\"}"}}]},"finish_reason":"tool_calls"}]}',
             'data: {"choices":[],"usage":{"completion_tokens":9},"timings":{"predicted_n":9}}', "data: [DONE]"]
    sent = []

    @contextlib.contextmanager
    def stream(method, url, json, timeout):
        sent.append(json)
        yield httpx.Response(200, request=httpx.Request(method, url), content="\n".join(lines).encode())
    monkeypatch.setattr(providers.httpx, "stream", stream)
    got = []
    r = providers.chat({"base_url": "http://test/v1"}, "m", [{"role": "user", "content": "ls"}],
                       on_delta=lambda kind, text: got.append((kind, text)))
    assert sent[0]["stream"] and got == [("reasoning", "think"), ("content", "Let me run it."),
                                         ("tool_call", '{"com'), ("tool_call", 'mand": "ls"}')]
    assert r.text == "Let me run it." and r.reasoning == "think" and r.finish == "tool_calls" and r.usage == {"completion_tokens": 9}
    assert r.tool_calls == [{"id": "c0", "type": "function", "function": {"name": "bash", "arguments": '{"command": "ls"}'}}]


def test_v1_streams_a_routed_expert_and_hands_client_tool_calls_back(tmp_path, monkeypatch):
    """The client runs its own tools: the first request ends with the expert's call, the second carries the result,
    reuses the route without asking the classifiers, and ships the reviewed answer as content."""
    from starlette.testclient import TestClient
    from lobes.lobe import executive
    monkeypatch.setattr(models.ModelManager, "validate", lambda self, names: {})
    monkeypatch.setattr(models.ModelManager, "ensure", lambda self, name: None)
    executive._seen.clear()
    replies = {"brick-2-max": [says("hard")], "granite-1b": [reply({"topic": "math"})],
               "nemotron-3-nano-4b": [calls(("bash", {"command": "python -c 'print(17*23)'"})), says("17 boxes hold 391 parts."),
                                      says("391 parts.")],
               "gemma4-e2b": [says("OK"), says("OK")]}
    seen = []

    def chat(provider, model, messages, on_delta=None, **kw):
        seen.append((model, [dict(m) for m in messages], kw))
        r = replies[model].pop(0)
        if on_delta:
            on_delta("reasoning", f"({model} thinks)")
            if r.text:
                on_delta("content", r.text)
        return r
    monkeypatch.setattr(providers, "chat", chat)
    bash = {"type": "function", "function": {"name": "bash", "parameters": {"type": "object"}}}
    ask = [{"role": "developer", "content": "You are a coding agent."},
           {"role": "user", "content": "A box holds 23 parts. How many parts are in 17 boxes?"}]
    # the replies follow the default relay, whatever relays the local lobes.yaml sets
    with TestClient(server.make_app(dict(config.load(), _root=tmp_path, relays={}))) as client:
        first = sse(client.post("/v1/chat/completions", json={"model": "lobes-v1", "stream": True, "tools": [bash],
                                                              "messages": ask}).text)
        call = next(c["choices"][0]["delta"]["tool_calls"] for c in first if c["choices"] and "tool_calls" in c["choices"][0]["delta"])
        tool_turn = [{"role": "assistant", "content": "", "reasoning_content": "(relay notes)",
                      "tool_calls": [{k: v for k, v in call[0].items() if k != "index"}]},
                     {"role": "tool", "tool_call_id": call[0]["id"], "content": "391"}]
        second = sse(client.post("/v1/chat/completions", json={"model": "lobes-v1", "stream": True, "tools": [bash],
                                                               "messages": ask + tool_turn}).text)
        compacted = [ask[0], {"role": "user", "content": "This is an automatically generated checkpoint: the user asked about boxes."}]
        third = sse(client.post("/v1/chat/completions", json={"model": "lobes-v1", "stream": True, "tools": [bash],
                                                              "messages": compacted + tool_turn}).text)
    assert [c["choices"][0]["finish_reason"] for c in first if c["choices"] and c["choices"][0]["finish_reason"]] == ["tool_calls"]
    assert not any(c["choices"][0]["delta"].get("content") for c in first if c["choices"])
    assert [m for m, *_ in seen[:5]] == ["granite-1b", "brick-2-max", "nemotron-3-nano-4b", "nemotron-3-nano-4b", "gemma4-e2b"]
    expert = seen[3][1]
    assert expert[0] == {"role": "system", "content": "You are a coding agent."} and expert[-1]["content"] == "391"
    assert "reasoning_content" not in expert[2] and seen[3][2]["tools"] == [bash]
    assert "python -c" in seen[4][1][-1]["content"] and "391" in seen[4][1][-1]["content"]
    thinking = "".join(c["choices"][0]["delta"].get("reasoning_content", "") for c in second if c["choices"])
    content = "".join(c["choices"][0]["delta"].get("content", "") for c in second if c["choices"])
    assert "[review]" in thinking and "17 boxes hold 391 parts." not in thinking and content == "17 boxes hold 391 parts."
    assert second[-1]["lobes"]["topic"] == "math" and second[-1]["usage"]["completion_tokens"] == 20
    assert second[-1]["usage"]["prompt_tokens"] == 20 and second[-1]["lobes"]["usage"]["prompt_tokens"] == 40
    assert [m for m, *_ in seen[5:]] == ["nemotron-3-nano-4b", "gemma4-e2b"] and third[-1]["lobes"]["route"] == "hard"


def test_api_stops_the_run_when_a_streaming_client_goes_away(tmp_path, monkeypatch):
    import asyncio
    import threading
    from lobes import api
    started, result = threading.Event(), {}

    def run(cfg, goal, *, on_delta=None, cancel=None, **kw):
        on_delta("reasoning", "thinking")
        started.set()
        result["cancelled"] = cancel.wait(5)
        return TaskState("t", goal, [])
    monkeypatch.setattr(api, "run", run)
    app = server.make_app(dict(config.load(), _root=tmp_path))
    body = json.dumps({"stream": True, "messages": [{"role": "user", "content": "hi"}]}).encode()
    inbox = [{"type": "http.request", "body": body, "more_body": False}]

    async def receive():
        if inbox:
            return inbox.pop(0)
        while not started.is_set():
            await asyncio.sleep(0.01)
        return {"type": "http.disconnect"}

    async def send(message):
        pass
    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"}, "http_version": "1.1", "method": "POST",
             "scheme": "http", "path": "/v1/chat/completions", "raw_path": b"/v1/chat/completions", "root_path": "",
             "query_string": b"", "headers": [(b"content-type", b"application/json")], "client": ("t", 1), "server": ("t", 80)}
    asyncio.run(app(scope, receive, send))
    assert result["cancelled"]


def test_a_cancelled_request_stops_mid_stream_and_makes_no_more_calls(tmp_path, monkeypatch):
    ctx = runner.Ctx(config.load(), "specialists", runner.Trace(tmp_path / "trace"), tmp_path)
    monkeypatch.setattr(ctx.mm, "ensure", lambda name: None)
    shown = []
    state = TaskState("t", "request", [], on_delta=lambda kind, text: shown.append(text))

    def chat(provider, model, messages, on_delta=None, **kw):
        on_delta("reasoning", "a")
        on_delta("tool_call", '{"cmd": "echo')
        state.cancel.set()
        on_delta("tool_call", ' again')        # a reply that is all tool call arguments stops too
        pytest.fail("the stream must stop")
    monkeypatch.setattr(providers, "chat", chat)
    with pytest.raises(task.BudgetExceeded):
        ctx.chat(state, "reasoning", [], thinking=1024)
    assert shown == ["a"]
    monkeypatch.setattr(providers, "chat", lambda *a, **kw: pytest.fail("no call after a cancel"))
    with pytest.raises(task.BudgetExceeded):
        ctx.chat(state, "language", [])


def test_cli_prints_code_verbatim(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from lobes import cli
    state = TaskState("t", "fix it", [])
    state.answer = "def mid(xs):\n    return xs[len(xs) // 2]"
    monkeypatch.setattr(runner, "run", lambda *a, **kw: state)
    result = CliRunner().invoke(cli.app, ["ask", "fix it"])
    assert result.exit_code == 0 and state.answer in result.output


def test_eval_judge():
    from lobes import eval as ev
    assert ev.judge("gsm8k", {"gold": "18"}, "3 apples and 6 pears.\n18") == (True, False)
    assert ev.judge("gsm8k", {"gold": "19"}, "3 apples and 6 pears.\n18") == (False, False)
    assert ev.code_block("Sure:\n```python\ndef f():\n    pass\n```\nDone.") == "def f():\n    pass\n"
    assert ev.code_block("    return 1\n") == "    return 1\n"


class FakeRouter:
    def __init__(self, names):
        self.state = {n: "unloaded" for n in names}
        self.log = []

    def get(self, url, **kw):
        return httpx.Response(200, request=httpx.Request("GET", url),
                              json={"data": [{"id": n, "status": {"value": s}} for n, s in self.state.items()]})

    def post(self, url, json, **kw):
        op = url.rsplit("/", 1)[1]
        self.state[json["model"]] = "loaded" if op == "load" else "unloaded"
        self.log.append((op, json["model"]))
        return httpx.Response(200, request=httpx.Request("POST", url), json={})


def test_budget_lru(monkeypatch):
    cfg = {"llama": {"host": "h", "port": 1, "vram_budget_mb": 6400},
           "models": {"r": {"vram_mb": 0, "resident": True}, "a": {"vram_mb": 3000}, "b": {"vram_mb": 3000}, "c": {"vram_mb": 2000}}}
    router = FakeRouter(cfg["models"])
    router.state["r"] = "loaded"
    monkeypatch.setattr(models, "httpx", router)
    monkeypatch.setattr(models.ModelManager, "vram_now_mb", staticmethod(lambda: None))
    monkeypatch.setattr(models.time, "sleep", lambda s: None)
    mm = models.ModelManager(cfg)
    mm.ensure("a"); mm.ensure("b")
    assert sorted(mm.loaded()) == ["a", "b", "r"]
    mm.ensure("c")                                   # a is the oldest non-resident one
    assert sorted(mm.loaded()) == ["b", "c", "r"] and ("unload", "a") in router.log
    mm.ensure("b"); mm.ensure("a")                   # now c is older than b
    assert sorted(mm.loaded()) == ["a", "b", "r"]
    assert [e[1] for e in mm.events].count("unload") == 2
    cfg["llama"]["vram_budget_mb"] = 100
    with pytest.raises(RuntimeError):
        models.ModelManager(cfg).ensure("c")


def test_raw_condition(monkeypatch):
    """R is the model alone: one call, the judge reads the free text, humaneval takes the fenced block"""
    from lobes import eval as ev
    sent = []
    text = {"reply": "3 apples and 6 pears.\n18"}
    monkeypatch.setattr(providers, "chat", lambda p, m, msgs, **kw:
                        (sent.append((msgs, kw)), Reply(text["reply"], None, None, {"total_tokens": 5}, 1, {}))[1])
    monkeypatch.setattr(models.ModelManager, "ensure", lambda self, name: None)
    cfg = dict(config.load(), seed=4)
    vram = type("V", (), {"peak": 0})()
    rec = ev.run_item(cfg, "R", 4, "gsm8k", {"id": "g1", "prompt": "how many", "gold": "18"}, vram)
    assert rec["correct"] and rec["calls"] == 1 and rec["swaps"] == 0
    assert sent[0][0][0]["content"].endswith(ev.RAW_TAIL) and sent[0][1]["seed"] == 4
    text["reply"] = "Sure:\n```python\ndef add(a, b):\n    return a + b\n```\nthat is all"
    he = {"id": "h1", "prompt": "add", "entry_point": "add", "source": "def add(a, b):\n", "test": "def check(c):\n    assert c(1, 2) == 3\n"}
    assert ev.run_item(cfg, "R", 4, "humaneval", he, vram)["correct"]


def test_eval_record(tmp_path, monkeypatch):
    from lobes import eval as ev
    source = "Here it is:\n```python\ndef add(a, b):\n    return a + b\n```"
    replies = {"executive": [says("hard")] * 2,
               "reasoning": [calls(("python", {"code": "print(17 * 23)"})), says("391"), says(source)],
               "language": [says("OK"), says("OK")]}
    monkeypatch.setattr(models.ModelManager, "validate", lambda self, names: {})
    monkeypatch.setattr(runner.Ctx, "chat", lambda self, state, lobe, messages, **kw: replies[lobe].pop(0))
    vram = type("V", (), {"peak": 0})()
    cfg = dict(config.load(), _root=tmp_path)
    rec = ev.run_item(cfg, "D", 0, "tools", {"id": "t1", "prompt": "what is 17 * 23", "answer": "391"}, vram)
    assert rec["correct"] and rec["route"] == "hard" and rec["tools"] == 1 and rec["sent_back"] == 0
    assert rec["capped"] is None and "error" not in rec
    he = {"id": "h1", "prompt": "add", "entry_point": "add", "source": "def add(a, b):\n", "test": "def check(c):\n    assert c(1, 2) == 3\n"}
    assert ev.run_item(cfg, "D", 0, "humaneval", he, vram)["correct"]


def test_ids_select_items_past_the_default_slice(monkeypatch):
    from lobes import eval as ev
    monkeypatch.setattr(ev, "load_suite", lambda suite: [{"id": f"{suite}-{i}"} for i in range(300)])
    for seed, quick in ((0, False), (1, False), (0, True)):
        assert dict(ev.plan("D", seed, quick, ["gsm8k"], {"gsm8k-250"}))["gsm8k"] == [{"id": "gsm8k-250"}]


def test_api_moves_a_harness_context_snapshot_out_of_the_request(tmp_path):
    from lobes.api import _messages
    from lobes.lobe import reasoning
    sent =[{"role": "developer", "content": "You are a coding agent."}, {"role": "user", "content": "hi"},
            {"role": "user", "content": [{"type": "text", "text": "Current runtime context. Approval policy: ask."}]}]
    out, goal, _ = _messages(sent, tmp_path)
    assert goal == "hi" and out == [{"role": "system", "content": "You are a coding agent.\n\nCurrent runtime context. Approval policy: ask."},
                                    {"role": "user", "content": "hi"}]
    call = [{"id": "c1", "type": "function", "function": {"name": "read", "arguments": "{}"}}]
    working = sent + [{"role": "assistant", "content": "", "tool_calls": call}, {"role": "tool", "tool_call_id": "c1", "content": "x"},
                      {"role": "user", "content": 'The approval policy changed from "ask" to "never" (changed by the user).'},
                      {"role": "user", "content": "Current runtime context. Approval policy: never."}]
    out, goal, _ = _messages(working, tmp_path)
    assert goal == "hi" and out[0]["content"].endswith("Approval policy: never.")
    from lobes.runner import request_at, ran_in_turn
    assert ran_in_turn(out) == [("read", {}, "x")]
    later = out + [{"role": "assistant", "content": "done"}, {"role": "user", "content": "now the tests"}]
    assert later[request_at(later)]["content"] == "now the tests"
    repeat = out + [{"role": "user", "content": "You are repeating the exact same tool call with identical arguments."}]
    assert request_at(repeat) == 1
    for notice in ("Another language model started to solve this problem and produced a summary.", "<environment_context>\n  <cwd>E:\\</cwd>",
                   "<turn_aborted>\nThe user interrupted the previous turn.", "# AGENTS.md instructions for E:\\"):
        assert request_at(out + [{"role": "user", "content": notice}]) == 1
    stopped = out + [{"role": "user", "content": "write a .bat instead"}]        # the tool step never got its reply
    assert request_at(stopped) == len(out) and request_at(stopped[:len(out)]) == 1
    compacted = [out[0], {"role": "user", "content": "This is an automatically generated checkpoint."}] + out[2:4]
    assert request_at(compacted) == 1
    state = TaskState("t", "今天是星期几", [])
    state.turn.now = "2026-09-16 Wednesday 22:07"
    assert reasoning.brief(state).endswith("(Local time: 2026-09-16 Wednesday 22:07)")


def test_a_tool_step_keeps_its_turns_route_and_send_backs(tmp_path):
    from lobes.lobe import executive
    ctx = runner.Ctx(dict(config.load(), _root=tmp_path), "v1", None, tmp_path)
    made = TaskState("t", "x", [], turn=Turn(route="hard", topic="math", now="then", problems=["wrong"]))
    made.tool_calls = [{"id": "k1"}]
    executive.remember(ctx, made)
    step = TaskState("t", "x", [], continues=True, step="k1")
    executive.intake(ctx, step)
    assert step.turn == made.turn and step.turn.problems is not made.turn.problems


def test_jsonl_line_separator(tmp_path):
    from lobes import eval as ev
    p = tmp_path / "x.jsonl"
    p.write_text(json.dumps({"a": "one two"}, ensure_ascii=False) + "\n", encoding="utf-8")
    assert ev.jsonl(p) == [{"a": "one two"}]
