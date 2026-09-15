"""No server needed: model calls and the router are faked. Run with `pytest`."""
import json

import pytest

from lobes import config, models, runner, tools
from lobes.lobe import Witness, agree, settle, verifier
from lobes.providers import Reply
from lobes.schema import Envelope, Next, Verdict


def test_envelope_roundtrip():
    e = Envelope(kind="final", goal="g", answer="42", next=Next(action="answer"))
    assert Envelope.model_validate_json(e.model_dump_json()) == e
    assert Verdict.model_validate({"verdict": "PASS"}).basis == "none"


def test_python_tool_timeout(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "TIMEOUT", 1)
    assert tools.run("python", {"code": "import time; time.sleep(5)"}, tmp_path)["exit"] == -1
    assert tools.run("python", {"code": "print(6*7)"}, tmp_path)["stdout"].strip() == "42"
    assert tools.run("nope", {}, tmp_path)["exit"] == 2


def test_agree_and_settle():
    assert verifier.same("The answer is 42.", "42") and verifier.same("1,000", "1000.0")
    assert not verifier.same("42", "43") and not verifier.same("Paris", "Berlin")
    goal = "20 footballs cost 5 each, how many and what total?"
    assert agree("40\n200", "40 footballs, total 200", goal) and agree("200", "the total is 200", goal)
    assert not agree("70", "40", goal) and agree("20", "20", goal) and not agree("", "20", goal)
    assert agree("Monday", "It is a Monday.", "what day") and not agree("Monday", "Tuesday", "what day")
    ws = [Witness("motor", "390", ran=True), Witness("reasoning", "391", ran=True), Witness("verifier", "391")]
    best, basis = settle(ws, 2, goal)
    assert best is ws[1] and basis == "evidence"
    assert settle(ws[:2], 2, goal) is None
    assert settle([ws[2]], 1, goal) == (ws[2], "none")
    assert settle([Witness("a", "Alice"), Witness("b", "Alice")], 2, "who")[1] == "consistency"


class FakeRouter:
    def __init__(self, names):
        self.state = {n: "unloaded" for n in names}
        self.log = []

    def get(self, url, **kw):
        return FakeResp({"data": [{"id": n, "status": {"value": s}} for n, s in self.state.items()]})

    def post(self, url, json, **kw):
        op = url.rsplit("/", 1)[1]
        self.state[json["model"]] = "loaded" if op == "load" else "unloaded"
        self.log.append((op, json["model"]))
        return FakeResp({})


class FakeResp:
    status_code = 200

    def __init__(self, j):
        self._j = j

    def json(self):
        return self._j

    def raise_for_status(self):
        pass


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


def _reply(data, tokens=10):
    return Reply(text=json.dumps(data), data=data, reasoning=None, usage={"total_tokens": tokens}, ms=1, timings={})


def _run(tmp_path, monkeypatch, goal, answers, **cfg_over):
    """runner.run with Ctx.chat replaced: answers[lobe] is a list of replies in call order (the last one repeats)
    or a callable(messages). The ladder is off unless the test turns it on, as in the eval.
    Returns (state, calls seen as lobe names, trace records, messages sent per lobe)."""
    cfg = config.load()
    cfg["_root"], cfg["no_escalate"] = tmp_path, True
    cfg.update(cfg_over)
    seen, sent = [], {}

    def fake_chat(self, state, lobe, messages, *, schema=None, thinking=None, temperature=0.2, max_tokens=2048, images=None):
        seen.append(lobe)
        sent.setdefault(lobe, []).append(messages)
        a = answers[lobe]
        if callable(a):
            r = _reply(a(messages))
        else:
            i = min(len(sent[lobe]) - 1, len(a) - 1)
            r = _reply(a[i]) if isinstance(a[i], dict) else a[i]
        state.usage["total_tokens"] = state.usage.get("total_tokens", 0) + r.usage["total_tokens"]
        state.calls.append((lobe, "fake", 1, r.usage["total_tokens"]))
        return r

    monkeypatch.setattr(runner.Ctx, "chat", fake_chat)
    state = runner.run(cfg, goal, profile="specialists", images=cfg_over.get("images"))
    trace = [json.loads(l) for l in (tmp_path / "runs" / state.task_id / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
    return state, seen, trace, sent


def _program(code):
    return {"why": "compute", "call": {"name": "python", "args": {"code": code}}}


def test_two_witnesses_agree(tmp_path, monkeypatch):
    """math: the motor lobe's program and the reasoning lobe's check print the same value, nobody else runs"""
    st, seen, trace, _ = _run(tmp_path, monkeypatch, "what is 17 * 23", {
        "motor": [_program("print(17*23)")],
        "reasoning": [{"answer": "391", "check": "print(17 * 23)"}],
        "language": [{"answer": "17 × 23 = 391"}]})
    assert st.task_class == "math" and st.route == "lobes"
    assert seen == ["motor", "reasoning", "language"]
    assert [(w.lobe, w.value, w.ran) for w in st.witnesses] == [("motor", "391", True), ("reasoning", "391", True)]
    assert st.basis == "evidence" and st.verdicts[-1].verdict == "PASS" and st.answer == "17 × 23 = 391"
    kinds = [t["kind"] for t in trace]
    assert kinds[:3] == ["start", "intake", "tool"] and kinds.count("witness") == 2 and kinds[-2:] == ["verdict", "final"]
    assert not st.observations                       # tool output is a witness's value, never shared


def test_third_witness_breaks_a_tie(tmp_path, monkeypatch):
    """the motor program is wrong; the verifier lobe, blind, sides with the reasoning lobe"""
    st, seen, _, sent = _run(tmp_path, monkeypatch, "what is 17 * 23", {
        "motor": [_program("print(17*22)")],
        "reasoning": [{"answer": "391", "check": "print(17 * 23)"}],
        "verifier": [{"answer": "391", "check": "print(17*23)"}],
        "language": [{"answer": "391"}]})
    assert seen == ["motor", "reasoning", "verifier", "language"]
    assert st.value == "391" and st.basis == "evidence" and "reasoning, verifier agree" in st.verdicts[-1].notes
    for lobe in ("reasoning", "verifier"):           # blind: no other witness's value in what they were sent
        assert "374" not in json.dumps(sent[lobe])


def test_no_majority_is_hedged(tmp_path, monkeypatch):
    from lobes.lobe import language
    st, seen, _, _ = _run(tmp_path, monkeypatch, "what is 17 * 23", {
        "motor": [_program("print(17*22)")],
        "reasoning": [{"answer": "391", "check": "print(17*23)"}],
        "verifier": [{"answer": "400", "check": None}],
        "language": [{"answer": "391"}]})
    assert st.verdicts[-1].verdict == "CONFLICT" and st.basis == "none"
    assert st.answer == language.HEDGE + "391" and len(st.uncertainties) == 2


def test_ladder_is_one_more_witness(tmp_path, monkeypatch):
    """with the ladder on, the escalate model answers blind after the local witnesses and can make the majority"""
    st, seen, trace, _ = _run(tmp_path, monkeypatch, "what is 17 * 23", {
        "motor": [_program("print(17*22)")],
        "reasoning": [{"answer": "391", "check": None}],
        "verifier": [{"answer": "400", "check": None}],
        "escalate": [{"answer": "391", "check": "print(17*23)"}],
        "language": [{"answer": "391"}]}, no_escalate=False)
    assert seen == ["motor", "reasoning", "verifier", "escalate", "language"] and st.escalations == 1
    assert st.basis == "evidence" and st.value == "391" and [t["to"] for t in trace if t["kind"] == "escalate"]


def test_program_repair_and_restatement(tmp_path, monkeypatch):
    """a program that dies gets one repair with its stderr; a check that just prints the answer is not evidence"""
    st, seen, _, sent = _run(tmp_path, monkeypatch, "what is 17 * 23", {
        "motor": [_program("print(17*23"), _program("print(17*23)")],
        "reasoning": [{"answer": "391", "check": "print(391)"}],
        "language": [{"answer": "391"}]})
    assert seen == ["motor", "motor", "reasoning", "language"] and st.retries == 1
    assert "SyntaxError" in sent["motor"][1][-1]["content"]
    assert [(w.ran, w.note) for w in st.witnesses] == [(True, ""), (False, "restated")]
    assert st.basis == "evidence" and st.value == "391"


def test_auto_climb(tmp_path, monkeypatch):
    """auto: no two witnesses ever agree, so the effort climbs to high and xhigh, drawing more hot samples,
    then gives up hedged; the eval config has no model ladder"""
    from lobes.lobe import language
    n = iter(range(1000, 2000))
    st, seen, trace, _ = _run(tmp_path, monkeypatch, "what is 17 * 23", {
        "motor": [_program("print(17*22)")],
        "reasoning": lambda m: {"answer": str(next(n)), "check": None},
        "verifier": [{"answer": "392", "check": None}],
        "language": [{"answer": "1000"}]}, effort="auto", no_escalate=True)
    assert [t["level"] for t in trace if t["kind"] == "effort"] == ["high", "xhigh"] and st.effort == "xhigh"
    assert len(st.witnesses) == runner.EFFORT["xhigh"]["n"] and seen.count("reasoning") == 6
    assert st.verdicts[-1].verdict == "CONFLICT" and st.answer == language.HEDGE + "1000"


def test_token_cap(tmp_path, monkeypatch):
    st, seen, _, _ = _run(tmp_path, monkeypatch, "what is 17 * 23", {
        "motor": [_reply(_program("print(17*22)"), tokens=7000)],
        "reasoning": [{"answer": "391", "check": None}],
        "language": [{"answer": "374"}]}, effort="low")
    assert seen == ["motor", "language"] and st.capped == "tokens"
    assert "capped by tokens" in st.verdicts[-1].notes and st.answer.startswith("Not sure")


def test_closed_book_unanimity(tmp_path, monkeypatch):
    """closed-book qa: every sample of the level must agree; one dissenter means a hedge"""
    from lobes.lobe import language
    exe = {"kind": "qa", "needs_tool": False}
    st, seen, _, _ = _run(tmp_path, monkeypatch, "who wrote it", {
        "executive": [exe], "reasoning": [{"answer": "Alice", "check": None}], "language": [{"answer": "Alice"}]})
    assert seen == ["executive", "reasoning", "reasoning", "reasoning", "language"]
    assert st.basis == "consistency" and st.answer == "Alice"
    st, seen, _, _ = _run(tmp_path, monkeypatch, "who wrote it", {
        "executive": [exe], "reasoning": [{"answer": "Alice", "check": None}, {"answer": "Bob", "check": None}, {"answer": "Alice", "check": None}],
        "language": [{"answer": "Alice"}]})
    assert st.basis == "none" and st.answer == language.HEDGE + "Alice"
    assert runner.effort({"effort": "auto"}) is runner.EFFORT["medium"]
    with pytest.raises(ValueError):
        runner.effort({"effort": "ultra"})


def test_vision_ocr_witness(tmp_path, monkeypatch):
    """the perception lobe answers, the ocr engine read the same thing: settled without the reasoning lobe"""
    from lobes.lobe import perception
    monkeypatch.setattr(perception, "ocr", lambda path: ["Total", "42"])
    img = tmp_path / "x.png"
    img.write_bytes(b"")
    st, seen, _, _ = _run(tmp_path, monkeypatch, "what is the total?", {
        "perception": [{"description": "a receipt", "text": "Total 42", "details": []}, {"answer": "42"}],
        "language": [{"answer": "The total is 42."}]}, images=[img])
    assert st.task_class == "vision" and seen == ["perception", "perception", "language"]
    assert [(w.lobe, w.ran) for w in st.witnesses] == [("perception", False), ("ocr", True)]
    assert st.basis == "evidence" and st.answer == "The total is 42."
    assert [o.source for o in st.observations] == ["lobe:perception", "tool:ocr"]


class FakeCtx:
    """Enough of runner.Ctx for the verifier: every lobe is a model, chat replays canned replies."""
    def __init__(self, tmp_path, replies, **cfg):
        self.workdir, self.rundir, self.replies, self.cfg = tmp_path, tmp_path, iter(replies), cfg
        self.effort = runner.effort(cfg)
        self.trace = type("T", (), {"write": staticmethod(lambda *a, **k: None)})

    def is_model(self, lobe):
        return True

    def chat(self, state, lobe, messages, **kw):
        return _reply(next(self.replies))


def _state(goal, task_class):
    st = runner.TaskState("t", goal, [])
    st.task_class = task_class
    return st


def test_verify_code(tmp_path):
    goal = 'def dbl(x):\n    """\n    >>> dbl(2)\n    4\n    """'
    v = verifier.verify_code(FakeCtx(tmp_path, []), _state(goal, "code"), "def dbl(x):\n    return 2 * x")
    assert (v.verdict, v.basis) == ("PASS", "evidence")                       # the task's own examples, no model
    v = verifier.verify_code(FakeCtx(tmp_path, []), _state(goal, "code"), "def dbl(x):\n    return x")
    assert v.verdict == "RETRY" and "0/1" in v.notes
    # a blind test that raises inside itself is the test's fault, one that asserts is the candidate's
    st = _state("write add(a, b)", "code")
    v = verifier.verify_code(FakeCtx(tmp_path, [{"test": "assert add(1, 2) == 3\nhelper()"}]), st, "def add(a, b):\n    return a + b")
    assert (v.verdict, v.basis) == ("PASS", "none") and "test itself broke" in v.notes
    v = verifier.verify_code(FakeCtx(tmp_path, [{"test": "assert add(1, 2) == 4"}]), st, "def add(a, b):\n    return a + b")
    assert v.verdict == "RETRY"
    v = verifier.verify_code(FakeCtx(tmp_path, []), _state("regex for a date", "code"), r"\d{4}-\d{2}-\d{2}")
    assert (v.verdict, v.basis) == ("PASS", "none")


def test_code_retry_carries_the_failure(tmp_path, monkeypatch):
    goal = 'implement this\n\ndef dbl(x):\n    """\n    >>> dbl(2)\n    4\n    """\n'
    st, seen, _, sent = _run(tmp_path, monkeypatch, goal, {
        "reasoning": [{"answer": "def dbl(x):\n    return x"}, {"answer": "def dbl(x):\n    return 2 * x"}]}, effort="low")
    assert st.task_class == "code" and seen == ["reasoning", "reasoning"] and st.retries == 1
    assert "rejected" in sent["reasoning"][1][-1]["content"] and "0/1" in sent["reasoning"][1][-1]["content"]
    assert st.basis == "evidence" and st.answer == "def dbl(x):\n    return 2 * x"


def test_seed_per_call(tmp_path, monkeypatch):
    """the eval fixes the seed; until the call index was folded in, every hot sample came back identical"""
    seeds = []
    monkeypatch.setattr(runner.providers, "chat",
                        lambda p, m, msgs, **kw: (seeds.append(kw["seed"]), Reply("{}", {}, None, {}, 1, {}))[1])
    monkeypatch.setattr(runner.ModelManager, "ensure", lambda self, name: None)
    cfg = config.load()
    cfg["_root"], cfg["seed"] = tmp_path, 7
    ctx = runner.Ctx(cfg, "specialists", runner.Trace(tmp_path / "trace.jsonl"), tmp_path)
    st = _state("who wrote it", "qa")
    for _ in range(3):
        ctx.chat(st, "reasoning", [])
    assert seeds == [7, 8, 9]


def test_raw_condition(monkeypatch):
    """R is the model alone: one call, the judge reads the free text, humaneval takes the fenced block"""
    from lobes import eval as ev
    sent = []
    reply = {"text": "3 apples and 6 pears.\n18"}
    monkeypatch.setattr(ev.providers, "chat",
                        lambda p, m, msgs, **kw: (sent.append((msgs, kw)), Reply(reply["text"], None, None, {"total_tokens": 5}, 1, {}))[1])
    monkeypatch.setattr(models.ModelManager, "ensure", lambda self, name: None)
    cfg = dict(config.load(), seed=4)
    vram = type("V", (), {"peak": 0})()
    rec = ev.run_item(cfg, "R", 4, "gsm8k", {"id": "g1", "prompt": "how many", "gold": "18"}, vram)
    assert rec["correct"] and rec["calls"] == 1 and rec["swaps"] == 0 and "level" not in rec
    assert sent[0][0][0]["content"].endswith(ev.RAW_TAIL) and sent[0][1]["seed"] == 4
    reply["text"] = "Sure:\n```python\ndef add(a, b):\n    return a + b\n```\nthat is all"
    he = {"id": "h1", "prompt": "add", "entry_point": "add", "source": "def add(a, b):\n", "test": "def check(c):\n    assert c(1, 2) == 3\n"}
    assert ev.run_item(cfg, "R", 4, "humaneval", he, vram)["correct"]


def test_eval_record(tmp_path, monkeypatch):
    from lobes import eval as ev
    cfg = config.load()
    cfg["_root"] = tmp_path
    replies = {"motor": _program("print(17*22)"), "reasoning": {"answer": "391", "check": "print(17*23)"},
               "verifier": {"answer": "391", "check": "print(17*23)"}, "language": {"answer": "391"}}
    monkeypatch.setattr(runner.Ctx, "chat", lambda self, state, lobe, messages, **kw: _reply(replies[lobe]))
    vram = type("V", (), {"peak": 0})()
    rec = ev.run_item(cfg, "D", 0, "tools", {"id": "t1", "prompt": "what is 17 * 23", "answer": "391"}, vram)
    assert rec["correct"] and rec["basis"] == "evidence" and rec["passed"] and not rec["stuck"] and rec["capped"] is None
    assert [w[0] for w in rec["witnesses"]] == ["motor", "reasoning", "verifier"] and rec["agreed"] == 2 and rec["disagree"]


def test_forced_answer(monkeypatch):
    """thinking that eats the whole cap gets a second, prefilled call that closes the think block and answers"""
    from lobes import providers
    bodies = []
    replies = [{"choices": [{"message": {"content": "", "reasoning_content": "so far 23*40=920"}, "finish_reason": "length"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 100, "total_tokens": 110}},
               {"choices": [{"message": {"content": '"answer": "1081"}'}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 120, "completion_tokens": 8, "total_tokens": 128}}]

    class Resp:
        def __init__(self, j): self.j = j
        def raise_for_status(self): pass
        def json(self): return self.j
    import copy
    monkeypatch.setattr(providers.httpx, "post",
                        lambda url, json, headers, timeout: (bodies.append(copy.deepcopy(json)), Resp(replies[len(bodies) - 1]))[1])
    r = providers.chat({"base_url": "http://x"}, "m", [{"role": "user", "content": "q"}], schema={"type": "object"}, thinking=True, max_tokens=100)
    assert r.forced and r.data == {"answer": "1081"} and r.usage["total_tokens"] == 238 and r.reasoning == "so far 23*40=920"
    last = bodies[1]["messages"][-1]
    assert last["role"] == "assistant" and last["reasoning_content"].endswith(providers.BUDGET_MSG) and last["content"] == "{"
    assert bodies[1]["max_tokens"] == 2500 and bodies[0]["max_tokens"] == 100


def test_hedge(tmp_path):
    from lobes.lobe import language
    ctx = FakeCtx(tmp_path, [])
    ctx.is_model = lambda lobe: False
    st = _state("who wrote it", "qa")
    st.value = "Alice"
    assert language.say(ctx, st).answer == language.HEDGE + "Alice"
    st.basis = "consistency"
    assert language.say(ctx, st).answer == "Alice"


def test_language_guard():
    from lobes.lobe.language import faithful
    assert faithful("17 x 23 = 391", "391", "what is 17 * 23")
    assert not faithful("97404784", "97405784", "what is 123456 * 789 minus 1000")
    assert faithful("The capital of Australia is Canberra.", "Canberra", "capital of australia?")
    assert not faithful("The capital is Sydney.", "Canberra", "capital of australia?")
    assert faithful("Sum 27660, count 110.", "27660\n110", "sum and count") and not faithful("27660", "27660\n110", "sum and count")
    assert faithful("Monday and Tuesday", "Monday\nTuesday", "which days") and not faithful("Tuesday", "Monday\nTuesday", "which days")


def test_fast_route(tmp_path, monkeypatch):
    cfg = config.load()
    cfg["_root"] = tmp_path
    monkeypatch.setattr(runner.Ctx, "chat", lambda self, state, lobe, messages, **kw:
                        Reply(text="hi there", data=None, reasoning=None, usage={}, ms=1, timings={}))
    state = runner.run(cfg, "hello", profile="specialists")
    assert state.route == "fast" and state.answer == "hi there" and state.steps == 0 and not state.verdicts


def test_jsonl_line_separator(tmp_path):
    from lobes import eval as ev
    p = tmp_path / "x.jsonl"
    p.write_text(json.dumps({"a": "one two"}, ensure_ascii=False) + "\n", encoding="utf-8")
    assert ev.jsonl(p) == [{"a": "one two"}]
