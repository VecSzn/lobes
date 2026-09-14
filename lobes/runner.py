"""The loop. Lobes are plain functions; this decides who runs next and writes everything down.

INTAKE -> FAST | PLAN -> ACT (motor or reasoning) -> TOOL -> ... -> VERIFY -> ANSWER
                                   ^                                  |
                                   +---- retry / check / escalate ----+
"""
import json
import time
import uuid
from dataclasses import dataclass, field

from . import config, providers, tools
from .models import ModelManager
from .schema import Envelope, Observation

MAX_STEPS, MAX_RETRIES, MAX_ESCALATIONS = 8, 2, 1


class Trace:
    def __init__(self, path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, kind, **fields):
        rec = {"t": round(time.time(), 3), "kind": kind, **fields}
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")


@dataclass
class TaskState:
    task_id: str
    goal: str
    images: list
    task_class: str = "qa"           # chat | math | code | vision | qa
    route: str = "plan"              # fast | plan
    needs_tool: bool = False
    plan: Envelope | None = None
    observations: list = field(default_factory=list)   # Observation
    tool_results: dict = field(default_factory=dict)   # ref -> raw result dict
    candidate: Envelope | None = None
    verdicts: list = field(default_factory=list)
    retries: int = 0
    escalations: int = 0
    steps: int = 0
    overrides: dict = field(default_factory=dict)      # lobe -> other lobe slot (escalation)
    swaps: int = 0
    calls: list = field(default_factory=list)          # (lobe, model, ms, tokens)
    answer: str | None = None
    t0: float = field(default_factory=time.perf_counter)

    def ms(self):
        return int((time.perf_counter() - self.t0) * 1000)

    def summary(self):
        toks = sum(c[3] for c in self.calls)
        return (f"{self.task_class} steps={self.steps} retries={self.retries} esc={self.escalations} "
                f"swaps={self.swaps} calls={len(self.calls)} tokens={toks} {self.ms()} ms")


class Ctx:
    def __init__(self, cfg, profile, trace, rundir):
        self.cfg, self.profile, self.trace, self.rundir = cfg, profile, trace, rundir
        self.workdir = rundir / "work"
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.mm = ModelManager(cfg)

    def slot(self, lobe):
        return config.lobe(self.cfg, lobe, self.profile)

    def is_model(self, lobe):
        return self.slot(lobe)[0] != "impl"

    def chat(self, state, lobe, messages, *, schema=None, thinking=None, temperature=0.2, max_tokens=2048, images=None):
        prov, model = self.slot(state.overrides.get(lobe, lobe))
        if prov == "local":
            n = len(self.mm.events)
            self.mm.ensure(model)
            for name, op, ms, vram in self.mm.events[n:]:
                self.trace.write("model", lobe=lobe, name=name, op=op, ms=ms, vram_mb=vram)
                state.swaps += op == "load"
        mcfg = self.cfg["models"].get(model, {})
        r = providers.chat(self.cfg["providers"][prov], model, messages, schema=schema, images=images,
                           thinking=thinking if mcfg.get("thinking") else None,
                           temperature=temperature, max_tokens=max_tokens)
        toks = r.usage.get("total_tokens", 0)
        state.calls.append((lobe, f"{prov}/{model}", r.ms, toks))
        self.trace.write("call", lobe=lobe, model=f"{prov}/{model}", ms=r.ms, tokens=toks, thinking=thinking,
                         temperature=temperature, parsed=r.data is not None if schema else None,
                         text=r.text[:4000], reasoning=(r.reasoning or "")[:2000])
        return r


def run_tools(ctx, state, calls):
    for c in calls:
        ref = f"tool_{len(state.tool_results)}"
        res = tools.run(c.name, c.args, ctx.workdir)
        (ctx.rundir / f"{ref}.json").write_text(json.dumps({"call": c.model_dump(), "result": res}, ensure_ascii=False, indent=1),
                                               encoding="utf-8")
        out = (res.get("stdout") or res.get("content") or "").strip()
        summary = out[:2000] if out else f"(no output) exit={res.get('exit')} {res.get('stderr', '')[:1500]}"
        state.tool_results[ref] = res
        state.observations.append(Observation(source=f"tool:{c.name}", ref=ref, summary=summary))
        ctx.trace.write("tool", ref=ref, call=c.model_dump(), exit=res.get("exit"), summary=summary[:500])


def run(cfg, goal, *, profile=None, images=None, task_id=None):
    from .lobe import executive, language, motor, perception, reasoning, verifier

    task_id = task_id or time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    rundir = cfg["_root"] / "runs" / task_id
    trace = Trace(rundir / "trace.jsonl")
    ctx = Ctx(cfg, profile or cfg["profile"], trace, rundir)
    state = TaskState(task_id, goal, [str(p) for p in images or []])
    trace.write("start", goal=goal, profile=ctx.profile, images=state.images)

    executive.intake(ctx, state)
    trace.write("intake", task_class=state.task_class, route=state.route)
    if state.route == "fast":
        state.candidate = executive.fast(ctx, state)
        return _finish(ctx, state, language)

    if state.images:
        perception.look(ctx, state)
    state.plan = executive.plan(ctx, state)
    trace.write("plan", plan=state.plan.model_dump())
    next_lobe = "motor" if state.plan.next.action == "tool" else "reasoning"

    while state.steps < MAX_STEPS:
        state.steps += 1
        if next_lobe == "motor":
            env = motor.act(ctx, state)
            run_tools(ctx, state, env.tool_calls)
            next_lobe = "reasoning"
            continue
        env = reasoning.solve(ctx, state)
        state.candidate = env
        if env.next.action == "tool" and env.tool_calls:
            run_tools(ctx, state, env.tool_calls)     # reasoning wants evidence before committing
            continue
        v = verifier.verify(ctx, state)
        state.verdicts.append(v)
        trace.write("verdict", **v.model_dump())
        if v.verdict == "PASS":
            break
        if v.verdict == "VERIFY_WITH_TOOL" and v.proposed_check:
            run_tools(ctx, state, [v.proposed_check])
            continue
        if state.retries < MAX_RETRIES:
            state.retries += 1          # reasoning.solve changes thinking/temperature with this
            continue
        if state.escalations < MAX_ESCALATIONS and ctx.is_model("escalate"):
            state.escalations += 1
            state.retries = 0
            state.overrides["reasoning"] = "escalate"
            trace.write("escalate", to=ctx.slot("escalate"))
            continue
        break                           # out of moves, answer with what we have and say so
    return _finish(ctx, state, language)


def _finish(ctx, state, language):
    final = language.say(ctx, state)
    state.answer = final.answer
    ctx.trace.write("final", answer=final.answer, confidence=final.confidence.model_dump(),
                    uncertainties=final.uncertainties, summary=state.summary())
    return state
