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
