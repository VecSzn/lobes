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

LADDER = ("escalate", "remote")      # who takes over reasoning once retries are used up, in this order
EFFORT = {   # reasoning_effort: when thinking turns on, its token cap, samples per vote, retries, steps, the ladder,
             # whether each candidate gets a reflection pass, and how many candidates the verifier scores per step
    "low":    dict(think="never",  budget=0,     n=1,  retries=1, steps=6,  ladder=False, reflect=False, width=1),
    "medium": dict(think="retry",  budget=6000,  n=3,  retries=2, steps=10, ladder=True,  reflect=False, width=1),
    "high":   dict(think="always", budget=16000, n=5,  retries=3, steps=14, ladder=True,  reflect=True,  width=2),
    "xhigh":  dict(think="always", budget=32000, n=8,  retries=4, steps=20, ladder=True,  reflect=True,  width=3),
    "max":    dict(think="always", budget=None,  n=12, retries=6, steps=30, ladder=True,  reflect=True,  width=4),   # None: the context is the cap
}
AUTO = ("medium", "high", "xhigh")   # effort: auto starts at the first and climbs one level each time the retries run out


def effort(cfg):
    level = cfg.get("effort") or "medium"
    if level == "auto":
        level = AUTO[0]
    if level not in EFFORT:
        raise ValueError(f"effort must be auto or one of {list(EFFORT)}, not {level!r}")
    return EFFORT[level]


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
    usage: dict = field(default_factory=dict)          # prompt/completion/total tokens summed over calls
    answer: str | None = None
    effort: str = "medium"           # the level the answer came from; auto climbs
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
        self.level = cfg.get("effort") or "medium"
        self.auto = self.level == "auto"
        self.effort = effort(cfg)
        if self.auto:
            self.level = AUTO[0]
        self.workdir = rundir / "work"
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.mm = ModelManager(cfg)

    def climb(self):
        """auto only: move up one effort level, or None at the top. Thinking longer comes before a bigger model."""
        if not self.auto or self.level == AUTO[-1]:
            return None
        self.level = AUTO[AUTO.index(self.level) + 1]
        self.effort = EFFORT[self.level]
        return self.level

    def slot(self, lobe):
        return config.lobe(self.cfg, lobe, self.profile)

    def is_model(self, lobe):
        """True when the profile fills this slot with a model we can actually call (remote ones need a key)."""
        try:
            prov, _ = self.slot(lobe)
        except KeyError:
            return False
        p = self.cfg["providers"].get(prov, {})
        return prov != "impl" and (not p.get("base_url", "").startswith("https://") or bool(p.get("api_key")))

    def chat(self, state, lobe, messages, *, schema=None, thinking=None, temperature=0.2, max_tokens=2048, images=None):
        prov, model = self.slot(state.overrides.get(lobe, lobe))
        if prov == "local":
            n = len(self.mm.events)
            self.mm.ensure(model)
            for name, op, ms, vram in self.mm.events[n:]:
                self.trace.write("model", lobe=lobe, name=name, op=op, ms=ms, vram_mb=vram)
                state.swaps += op == "load"
        mcfg = self.cfg["models"].get(model, {})
        seed = self.cfg.get("seed")   # one seed per call: with the same seed every hot sample came back identical
        r = providers.chat(self.cfg["providers"][prov], model, messages, schema=schema, images=images,
                           thinking=thinking if mcfg.get("thinking") else None, temperature=temperature,
                           max_tokens=max_tokens, seed=None if seed is None else seed + len(state.calls),
                           ctx=self.cfg.get("llama", {}).get("ctx"))
        toks = r.usage.get("total_tokens", 0)
        for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
            state.usage[k] = state.usage.get(k, 0) + r.usage.get(k, 0)
        state.calls.append((lobe, f"{prov}/{model}", r.ms, toks))
        self.trace.write("call", lobe=lobe, model=f"{prov}/{model}", ms=r.ms, tokens=toks, thinking=thinking,
                         temperature=temperature, parsed=r.data is not None if schema else None, finish=r.finish,
                         forced=r.forced, text=r.text[:4000], reasoning=(r.reasoning or "")[:2000])
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
        if res.get("image") and ctx.is_model("perception"):
            from .lobe import perception
            perception.look(ctx, state, images=[res["image"]])


def run(cfg, goal, *, profile=None, images=None, task_id=None):
    from .lobe import executive, language, motor, perception, reasoning, verifier

    task_id = task_id or time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    rundir = cfg["_root"] / "runs" / task_id
    trace = Trace(rundir / "trace.jsonl")
    ctx = Ctx(cfg, profile or cfg["profile"], trace, rundir)
    state = TaskState(task_id, goal, [str(p) for p in images or []])
    state.effort = ctx.level
    trace.write("start", goal=goal, profile=ctx.profile, images=state.images, effort=cfg.get("effort") or "medium")

    executive.intake(ctx, state)
    trace.write("intake", task_class=state.task_class, route=state.route)
    if state.route == "fast":
        state.candidate = executive.fast(ctx, state)
        return _finish(ctx, state, language)

    if state.images and ctx.is_model("perception"):
        perception.look(ctx, state)
    state.plan = executive.plan(ctx, state)
    trace.write("plan", plan=state.plan.model_dump())
    next_lobe = "motor" if state.plan.next.action == "tool" else "reasoning"
    rungs = [] if cfg.get("no_escalate") or not ctx.effort["ladder"] else [s for s in LADDER if ctx.is_model(s)]   # eval turns the ladder off

    while state.steps < ctx.effort["steps"]:
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
        v = _judge(ctx, state, reasoning, verifier)
        state.verdicts.append(v)
        trace.write("verdict", **v.model_dump())
        if v.verdict == "PASS":
            if v.answer:
                state.candidate.answer = v.answer
            break
        if v.verdict == "VERIFY_WITH_TOOL" and v.proposed_check:
            run_tools(ctx, state, [v.proposed_check])
            continue
        if state.retries < ctx.effort["retries"]:
            state.retries += 1          # reasoning.solve changes thinking/temperature with this
            continue
        if ctx.climb():
            state.retries, state.effort = 0, ctx.level
            trace.write("effort", level=ctx.level)
            continue
        if state.escalations < len(rungs):
            state.overrides["reasoning"] = rungs[state.escalations]
            state.escalations += 1
            state.retries = 0
            trace.write("escalate", to=ctx.slot(state.overrides["reasoning"]))
            continue
        break                           # out of moves, answer with what we have and say so
    return _finish(ctx, state, language)


def _judge(ctx, state, reasoning, verifier):
    """One candidate goes straight to the verifier. At width > 1 the verifier scores that many and the best stays
    as the candidate: a beam one step wide, with the verdict as the value. Vision skips it, each candidate there
    already costs three readers."""
    e = ctx.effort
    cands = [state.candidate]
    if e["width"] > 1 and not state.images:
        more = (reasoning.sample(ctx, state) for _ in range(e["width"] - 1))
        cands += [c for c in more if c.next.action != "tool"]
    scored = []
    for c in cands:
        state.candidate = c
        if e["reflect"]:
            reasoning.reflect(ctx, state)
        v = verifier.verify(ctx, state)
        scored.append((verifier.score(v), c, v))
    if len(scored) > 1:
        ctx.trace.write("search", answers=[c.answer for _, c, _ in scored], scores=[s for s, _, _ in scored])
    _, state.candidate, v = max(scored, key=lambda t: t[0])     # ties go to the first, the cold sample
    return v


def _finish(ctx, state, language):
    final = language.say(ctx, state)
    state.answer = final.answer
    ctx.trace.write("final", answer=final.answer, confidence=final.confidence.model_dump(),
                    uncertainties=final.uncertainties, summary=state.summary())
    return state
