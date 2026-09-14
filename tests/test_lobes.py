"""No server needed: model calls and the router are faked. Run with `pytest`."""
import json

import pytest

from lobes import config, models, runner, tools
from lobes.lobe import verifier
from lobes.providers import Reply
from lobes.schema import Claim, Envelope, Next, Verdict


def test_envelope_roundtrip():
    e = Envelope(kind="final", goal="g", answer="42", next=Next(action="answer"),
                 claims=[Claim(id="c1", text="42", support="tool", evidence="tool_0")])
    assert Envelope.model_validate_json(e.model_dump_json()) == e
    assert Verdict.model_validate({"verdict": "PASS"}).basis == "none"


def test_python_tool_timeout(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "TIMEOUT", 1)
    assert tools.run("python", {"code": "import time; time.sleep(5)"}, tmp_path)["exit"] == -1
    assert tools.run("python", {"code": "print(6*7)"}, tmp_path)["stdout"].strip() == "42"
    assert tools.run("nope", {}, tmp_path)["exit"] == 2


def test_same_and_evidence():
    assert verifier.same("The answer is 42.", "42") and verifier.same("1,000", "1000.0")
    assert not verifier.same("42", "43") and not verifier.same("Paris", "Berlin")
    state = runner.TaskState("t", "what is 17 * 23", [])
    state.task_class = "math"
    state.tool_results = {"tool_0": {"stdout": "391\n", "exit": 0}}
    state.candidate = Envelope(kind="step_result", goal=state.goal, answer="391", next=Next(action="answer"),
                               claims=[Claim(id="c1", text="17 * 23 = 391", support="tool", evidence="tool_0")])
    assert verifier.evidence(state) == ([], "")
    state.candidate.claims[0].text = "17 * 23 = 392"
    assert verifier.evidence(state)[0] == ["c1"]
    state.candidate.claims[0].evidence = "tool_9"
    assert "not a tool output" in verifier.evidence(state)[1]
    state.candidate.claims = []
    state.candidate.answer = "400"
    assert verifier.evidence(state)[0] == ["answer"]


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


def _reply(data):
    return Reply(text=json.dumps(data), data=data, reasoning=None, usage={"total_tokens": 10}, ms=1, timings={})


def test_state_machine(tmp_path, monkeypatch):
    """math task: plan -> motor tool -> reasoning -> verifier disagrees and proposes a check -> reasoning -> PASS"""
    cfg = config.load()
    cfg["_root"] = tmp_path
    seen = []
    verifier_answers = iter(["390", "391"])

    def fake_chat(self, state, lobe, messages, *, schema=None, thinking=None, temperature=0.2, max_tokens=2048, images=None):
        seen.append(lobe)
        if lobe == "executive":
            return _reply({"steps": ["compute", "check"], "first": "reason"})
        if lobe == "motor":
            return _reply({"why": "compute it", "call": {"name": "python", "args": {"code": "print(17*23)"}}})
        if lobe == "reasoning":
            return _reply({"kind": "step_result", "goal": state.goal, "answer": "391", "next": {"action": "answer"},
                           "claims": [{"id": "c1", "text": "17 * 23 = 391", "support": "tool", "evidence": "tool_0"}]})
        if lobe == "verifier":
            return _reply({"answer": next(verifier_answers), "check": {"name": "python", "args": {"code": "print(17*23)"}}})
        if lobe == "language":
            return _reply({"answer": "17 × 23 = 391"})
        raise AssertionError(lobe)

    monkeypatch.setattr(runner.Ctx, "chat", fake_chat)
    state = runner.run(cfg, "what is 17 * 23", profile="specialists")
    assert state.task_class == "math" and state.route == "plan"
    assert seen == ["executive", "motor", "reasoning", "verifier", "reasoning", "verifier", "language"]
    assert [v.verdict for v in state.verdicts] == ["VERIFY_WITH_TOOL", "PASS"]
    assert state.verdicts[-1].basis == "consistency"
    assert state.answer == "17 × 23 = 391" and len(state.tool_results) == 2
    kinds = [json.loads(l)["kind"] for l in (tmp_path / "runs" / state.task_id / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
    assert kinds[:4] == ["start", "intake", "plan", "tool"] and kinds[-1] == "final" and kinds.count("verdict") == 2


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


def _state(goal, answer, task_class, tools_out=None, images=()):
    st = runner.TaskState("t", goal, list(images))
    st.task_class = task_class
    st.tool_results = {f"tool_{i}": {"stdout": o, "exit": 0} for i, o in enumerate(tools_out or [])}
    st.candidate = Envelope(kind="step_result", goal=goal, answer=answer, next=Next(action="answer"))
    return st


def test_verifier_paths(tmp_path):
    goal = 'def dbl(x):\n    """\n    >>> dbl(2)\n    4\n    """'
    v = verifier.verify(FakeCtx(tmp_path, []), _state(goal, "def dbl(x):\n    return 2 * x", "code"))
    assert (v.verdict, v.basis) == ("PASS", "evidence")                       # the task's own examples, no model
    v = verifier.verify(FakeCtx(tmp_path, []), _state(goal, "def dbl(x):\n    return x", "code"))
    assert v.verdict == "RETRY" and "0/1" in v.notes
    # a blind test that raises inside itself is the test's fault, one that asserts is the candidate's
    st = _state("write add(a, b)", "def add(a, b):\n    return a + b", "code")
    v = verifier.verify(FakeCtx(tmp_path, [{"test": "assert add(1, 2) == 3\nhelper()"}]), st)
    assert (v.verdict, v.basis) == ("PASS", "none") and "test itself broke" in v.notes
    v = verifier.verify(FakeCtx(tmp_path, [{"test": "assert add(1, 2) == 4"}]), st)
    assert v.verdict == "RETRY"
    # a bare number on a task misfiled as code is not code: blind re-solve, and its answer wins when a tool printed it
    st = _state("write 12345 in base 7", "240114", "code", ["50664"])
    v = verifier.verify(FakeCtx(tmp_path, []), st)
    assert v.verdict == "RETRY" and "not appear" in v.notes                    # evidence() sees the new number first
    st.retries = 1
    st.candidate.answer = "the result is 240114 in base 7"
    v = verifier.verify(FakeCtx(tmp_path, [{"answer": "50664", "check": None}]), st)
    assert (v.verdict, v.basis, v.answer) == ("PASS", "evidence", "50664")
    # closed-book trivia: disagreement without anything to check is not a conflict
    st = _state("who wrote it", "Alice", "qa")
    v = verifier.verify(FakeCtx(tmp_path, [{"answer": "Bob", "check": None}]), st)
    assert (v.verdict, v.basis) == ("PASS", "none")
    st.candidate.confidence.basis = "consistency"
    v = verifier.verify(FakeCtx(tmp_path, [{"answer": "Bob", "check": None}]), st)
    assert (v.verdict, v.basis) == ("PASS", "consistency")


def test_effort_samples(tmp_path):
    """closed-book qa draws as many samples as the level says; unanimity is the only route to consistency"""
    from lobes.lobe import reasoning
    env = {"kind": "step_result", "goal": "g", "answer": "Alice", "next": {"action": "answer"}, "claims": []}
    for level, n in (("low", 1), ("medium", 3), ("high", 5), ("max", 12)):
        ctx = FakeCtx(tmp_path, [env] * n, effort=level)
        out = reasoning.solve(ctx, _state("who wrote it", None, "qa"))
        assert next(ctx.replies, None) is None, level                    # every reply consumed, no extra call made
        assert (out.confidence.basis == "consistency") == (n > 1), level
    ctx = FakeCtx(tmp_path, [env, dict(env, answer="Bob"), env], effort="medium")
    assert reasoning.solve(ctx, _state("who wrote it", None, "qa")).confidence.basis == "self"
    assert runner.effort({"effort": "auto"}) is runner.EFFORT["medium"]
    with pytest.raises(ValueError):
        runner.effort({"effort": "ultra"})


def test_seed_per_call(tmp_path, monkeypatch):
    """the eval fixes the seed; until the call index was folded in, every hot sample came back identical"""
    seeds = []
    monkeypatch.setattr(runner.providers, "chat",
                        lambda p, m, msgs, **kw: (seeds.append(kw["seed"]), Reply("{}", {}, None, {}, 1, {}))[1])
    monkeypatch.setattr(runner.ModelManager, "ensure", lambda self, name: None)
    cfg = config.load()
    cfg["_root"], cfg["seed"] = tmp_path, 7
    ctx = runner.Ctx(cfg, "specialists", runner.Trace(tmp_path / "trace.jsonl"), tmp_path)
    st = _state("who wrote it", None, "qa")
    for _ in range(3):
        ctx.chat(st, "reasoning", [])
    assert seeds == [7, 8, 9]


def test_reflect_and_score(tmp_path):
    from lobes.lobe import reasoning
    st = _state("who wrote it", "Alice", "qa")
    reasoning.reflect(FakeCtx(tmp_path, [{"flaw": "wrong person", "answer": "Bob"}], effort="high"), st)
    assert st.candidate.answer == "Bob" and "wrong person" in st.candidate.uncertainties[-1]
    reasoning.reflect(FakeCtx(tmp_path, [{"flaw": None, "answer": "Carol"}], effort="high"), st)
    assert st.candidate.answer == "Bob"                                      # no flaw named, no change
    st = _state("what is 17 * 23", "391", "math", ["391"])
    reasoning.reflect(FakeCtx(tmp_path, [], effort="high"), st)              # tool-backed: not even asked
    assert st.candidate.answer == "391"
    assert verifier.score(Verdict(verdict="PASS", basis="evidence")) > verifier.score(Verdict(verdict="PASS")) \
        > verifier.score(Verdict(verdict="VERIFY_WITH_TOOL")) > verifier.score(Verdict(verdict="RETRY"))


def test_auto_climb_and_search(tmp_path, monkeypatch):
    """auto: a math answer the tool contradicts is retried at medium, then the effort climbs to high and xhigh
    (search and reflection on) before the run gives up; the eval config has no model ladder"""
    from lobes.lobe import language, reasoning
    cfg = config.load()
    cfg["_root"], cfg["effort"], cfg["no_escalate"] = tmp_path, "auto", True

    def fake_chat(self, state, lobe, messages, *, schema=None, thinking=None, temperature=0.2, max_tokens=2048, images=None):
        if lobe == "executive":
            return _reply({"steps": ["compute"], "first": "tool"})
        if lobe == "motor":
            return _reply({"why": "compute", "call": {"name": "python", "args": {"code": "print(17*23)"}}})
        if lobe == "language":
            return _reply({"answer": "392"})
        if schema is reasoning.REFLECT:
            return _reply({"flaw": "the tool printed 391", "answer": "392"})     # names a flaw, keeps the answer
        if lobe == "reasoning":
            return _reply({"kind": "step_result", "goal": state.goal, "answer": "392", "next": {"action": "answer"},
                           "claims": [{"id": "c1", "text": "17 * 23 = 392", "support": "tool", "evidence": "tool_0"}]})
        raise AssertionError(lobe)

    monkeypatch.setattr(runner.Ctx, "chat", fake_chat)
    state = runner.run(cfg, "what is 17 * 23", profile="specialists")
    trace = [json.loads(l) for l in (tmp_path / "runs" / state.task_id / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [t["level"] for t in trace if t["kind"] == "effort"] == ["high", "xhigh"] and state.effort == "xhigh"
    assert all(v.verdict == "RETRY" for v in state.verdicts) and len(state.verdicts) == 3 + 4 + 5
    assert any(t["kind"] == "search" for t in trace) and any(t["kind"] == "reflect" for t in trace)
    assert state.answer == language.HEDGE + "392"


def test_hedge_and_override(tmp_path):
    from lobes.lobe import language
    ctx = FakeCtx(tmp_path, [])
    ctx.is_model = lambda lobe: False
    st = _state("who wrote it", "Alice", "qa")
    st.verdicts = [Verdict(verdict="PASS", basis="none")]
    assert language.say(ctx, st).answer == language.HEDGE + "Alice"
    st.verdicts = [Verdict(verdict="PASS", basis="consistency")]
    assert language.say(ctx, st).answer == "Alice"


def test_language_guard():
    from lobes.lobe.language import faithful
    assert faithful("17 x 23 = 391", "391", "what is 17 * 23")
    assert not faithful("97404784", "97405784", "what is 123456 * 789 minus 1000")
    assert faithful("The capital of Australia is Canberra.", "Canberra", "capital of australia?")
    assert not faithful("The capital is Sydney.", "Canberra", "capital of australia?")


def test_fast_route(tmp_path, monkeypatch):
    cfg = config.load()
    cfg["_root"] = tmp_path
    monkeypatch.setattr(runner.Ctx, "chat", lambda self, state, lobe, messages, **kw:
                        Reply(text="hi there", data=None, reasoning=None, usage={}, ms=1, timings={}))
    state = runner.run(cfg, "hello", profile="specialists")
    assert state.route == "fast" and state.answer == "hi there" and state.steps == 0 and not state.verdicts
