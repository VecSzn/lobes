"""The loop. Lobes are plain functions; this decides who runs next and writes everything down.

INTAKE -> FAST | LOOK -> WITNESS, WITNESS, ... until enough agree -> ANSWER
                          code: IMPLEMENT -> examples or a blind test -> retry with the failure attached

A witness sees the goal (and the image notes), never another witness. Two agreeing is the answer.
"""
import json
import time
import uuid
from dataclasses import dataclass, field

from . import config, providers, tools
from .lobe import Witness, agree, settle
from .models import ModelManager
from .schema import Verdict

EFFORT = {   # think: thinking on, budget: its token cap, n: witnesses an item may draw, retries: program repairs
    #          per witness, then the caps per item: witnesses (steps), model calls, tokens, seconds. None: no cap
    "low":    dict(think=False, budget=0,     n=1,  retries=1, steps=4,  calls=8,  tokens=6000,  seconds=120),
    "medium": dict(think=True,  budget=6000,  n=3,  retries=2, steps=8,  calls=16, tokens=16000, seconds=300),
    "high":   dict(think=True,  budget=16000, n=5,  retries=3, steps=12, calls=24, tokens=40000, seconds=600),
    "xhigh":  dict(think=True,  budget=32000, n=8,  retries=4, steps=18, calls=36, tokens=80000, seconds=1200),
    "max":    dict(think=True,  budget=None,  n=12, retries=6, steps=30, calls=60, tokens=None,  seconds=None),
}
AUTO = ("medium", "high", "xhigh")   # effort: auto starts at the first and climbs one level when the witnesses run out


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
    task_class: str = "qa"           # chat | code | vision | qa
    route: str = "lobes"             # fast | lobes
    needs_tool: bool = False
    observations: list = field(default_factory=list)   # Observation: what perception and ocr read from images
    tool_results: dict = field(default_factory=dict)   # ref -> raw result dict
    witnesses: list = field(default_factory=list)      # Witness, in the order they ran
    value: str | None = None         # the settled value, before the language lobe
    basis: str = "none"              # evidence | consistency | none
    feedback: str = ""               # code: why the last implementation was rejected
    capped: str | None = None        # which cap ended the item, if one did
    uncertainties: list = field(default_factory=list)
    verdicts: list = field(default_factory=list)       # one final Verdict, PASS or CONFLICT
    retries: int = 0
    steps: int = 0
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
        return (f"{self.task_class} witnesses={len(self.witnesses)} basis={self.basis} retries={self.retries} "
                f"swaps={self.swaps} calls={len(self.calls)} tokens={toks} {self.ms()} ms"
                + (f" capped={self.capped}" if self.capped else ""))


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
        """auto only: move up one effort level, or None at the top."""
        if not self.auto or self.level == AUTO[-1]:
            return None
        self.level = AUTO[AUTO.index(self.level) + 1]
        self.effort = EFFORT[self.level]
        return self.level

    def slot(self, lobe):
        return config.lobe(self.cfg, lobe, self.profile)

    def is_model(self, lobe):
        """True when the profile fills this slot with a model."""
        try:
            prov, _ = self.slot(lobe)
        except KeyError:
            return False
        return prov != "impl"

    def chat(self, state, lobe, messages, *, schema=None, thinking=None, temperature=0.2, max_tokens=2048, images=None):
        prov, model = self.slot(lobe)
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
                           ctx=mcfg.get("ctx") or self.cfg.get("llama", {}).get("ctx"))
        toks = r.usage.get("total_tokens", 0)
        for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
            state.usage[k] = state.usage.get(k, 0) + r.usage.get(k, 0)
        state.calls.append((lobe, f"{prov}/{model}", r.ms, toks))
        self.trace.write("call", lobe=lobe, model=f"{prov}/{model}", ms=r.ms, tokens=toks, thinking=thinking,
                         temperature=temperature, parsed=r.data is not None if schema else None, finish=r.finish,
                         forced=r.forced, text=r.text[:4000], reasoning=(r.reasoning or "")[:2000])
        return r


def run_tool(ctx, state, call):
    """Runs one call for a witness. -> (ref, result, output); output is empty unless the call succeeded. The
    result goes to the run dir and the trace, not to the observations: no other witness gets to see it."""
    ref = f"tool_{len(state.tool_results)}"
    res = tools.run(call.name, call.args, ctx.workdir)
    (ctx.rundir / f"{ref}.json").write_text(json.dumps({"call": call.model_dump(), "result": res}, ensure_ascii=False, indent=1),
                                           encoding="utf-8")
    out = (res.get("stdout") or res.get("content") or "").strip()
    state.tool_results[ref] = res
    ctx.trace.write("tool", ref=ref, call=call.model_dump(), exit=res.get("exit"), out=out[:500], stderr=(res.get("stderr") or "")[-300:])
    if res.get("image") and ctx.is_model("perception"):
        from .lobe import perception
        perception.look(ctx, state, images=[res["image"]])
    return ref, res, out if res.get("exit") == 0 else ""


def capped(ctx, state):
    e = ctx.effort
    if state.steps >= e["steps"]:
        state.capped = "steps"
    elif e["calls"] and len(state.calls) >= e["calls"]:
        state.capped = "calls"
    elif e["tokens"] and state.usage.get("total_tokens", 0) >= e["tokens"]:
        state.capped = "tokens"
    elif e["seconds"] and state.ms() >= e["seconds"] * 1000:
        state.capped = "seconds"
    return state.capped


def _plan(ctx, state):
    """Who derives the answer, in order, as (lobe, callable). The cheap ones go first because the loop stops as
    soon as enough agree; hot samples of the reasoning lobe fill up to n."""
    from .lobe import motor, perception, reasoning
    n = ctx.effort["n"]
    hot = ("reasoning", lambda: reasoning.witness(ctx, state, temperature=0.7))
    if state.task_class == "vision":
        plan = [("perception", lambda: perception.ask(ctx, state)), ("reasoning", lambda: reasoning.witness(ctx, state))]
    elif state.needs_tool:
        plan = [("motor", lambda: motor.witness(ctx, state)), ("reasoning", lambda: reasoning.witness(ctx, state)),
                ("verifier", lambda: reasoning.witness(ctx, state, lobe="verifier", thinking=False))]
    else:
        plan = [("reasoning", lambda: reasoning.witness(ctx, state))]
    plan += [hot] * (n - len(plan))
    return [(lobe, fn) for lobe, fn in plan if ctx.is_model(lobe)]


def _need(ctx, state):
    """Closed-book answers need every sample to agree; anything a program can settle needs two."""
    if state.task_class == "vision" or state.needs_tool or any(w.ran for w in state.witnesses):
        return 2
    return ctx.effort["n"]


def _witnesses(ctx, state):
    from .lobe import verifier
    i, hit = 0, None
    while True:
        plan = _plan(ctx, state)
        while i < len(plan) and not capped(ctx, state):
            lobe, make = plan[i]
            i += 1
            state.steps += 1
            w = make()
            state.witnesses.append(w)
            ctx.trace.write("witness", lobe=w.lobe, value=(w.value or "")[:500], ran=w.ran, ref=w.ref, note=w.note)
            if state.images and w.value and (ref := verifier.ocr_backed(state, w.value)):
                state.witnesses.append(Witness("ocr", w.value, ran=True, ref=ref))   # the engine read the same thing
            hit = settle(state.witnesses, _need(ctx, state), state.goal)
            if hit:
                break
        if hit or state.capped or not ctx.climb():
            break
        state.effort = ctx.level
        ctx.trace.write("effort", level=ctx.level)
    live = [w for w in state.witnesses if w.value]
    if hit:
        best, state.basis = hit
        state.value = best.value
        peers = [w.lobe for w in live if w is best or agree(best.value, w.value, state.goal)]
        v = Verdict(verdict="PASS", basis=state.basis, notes=f"{', '.join(peers)} agree on {best.value[:100]!r}")
    else:
        # no majority: the reasoning lobe's value goes out hedged, the others as uncertainties
        best = next((w for w in live if w.lobe == "reasoning"), live[0] if live else None)
        state.value = best.value if best else None
        state.uncertainties = [f"the {w.lobe} lobe got {w.value[:80]!r}" for w in live if w is not best][:3]
        v = Verdict(verdict="CONFLICT", failed_claims=["answer"],
                    notes=f"no {_need(ctx, state)} of {len(live)} witnesses agree" + (f"; capped by {state.capped}" if state.capped else ""))
    state.verdicts.append(v)
    ctx.trace.write("verdict", **v.model_dump())


def _code(ctx, state):
    """Implement, run the task's examples (or a blind test), and re-implement with the failure attached."""
    from .lobe import reasoning, verifier
    code, v = None, Verdict(verdict="CONFLICT", failed_claims=["answer"], notes="no implementation")
    for _ in range(ctx.effort["retries"] + 1):
        if capped(ctx, state):
            break
        state.steps += 1
        code = verifier.unfence(reasoning.code(ctx, state).answer)
        v = verifier.verify_code(ctx, state, code)
        state.witnesses.append(Witness("reasoning", code, ran=v.basis == "evidence", note=v.notes[:300]))
        ctx.trace.write("verdict", **v.model_dump())
        if v.verdict == "PASS":
            break
        state.feedback, state.retries = v.notes[:600], state.retries + 1
    state.value, state.basis = code, v.basis if v.verdict == "PASS" else "none"
    state.verdicts.append(v)


def run(cfg, goal, *, profile=None, images=None, task_id=None):
    from .lobe import executive, language, perception

    task_id = task_id or time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    rundir = cfg["_root"] / "runs" / task_id
    trace = Trace(rundir / "trace.jsonl")
    ctx = Ctx(cfg, profile or cfg["profile"], trace, rundir)
    state = TaskState(task_id, goal, [str(p) for p in images or []])
    state.effort = ctx.level
    trace.write("start", goal=goal, profile=ctx.profile, images=state.images, effort=cfg.get("effort") or "medium")

    executive.intake(ctx, state)
    trace.write("intake", task_class=state.task_class, route=state.route, needs_tool=state.needs_tool)
    if state.route == "fast":
        state.value = executive.fast(ctx, state).answer
        return _finish(ctx, state, language)
    if state.images and ctx.is_model("perception"):
        perception.look(ctx, state)
    if state.task_class == "code":
        _code(ctx, state)
    else:
        _witnesses(ctx, state)
    return _finish(ctx, state, language)


def _finish(ctx, state, language):
    final = language.say(ctx, state)
    state.answer = final.answer
    ctx.trace.write("final", answer=final.answer, confidence=final.confidence.model_dump(),
                    uncertainties=final.uncertainties, summary=state.summary())
    return state
