"""Relay a request through small specialists as plain text, under one shared request budget."""
import json
import re
import threading
import time
import uuid
from dataclasses import dataclass, field

import httpx

from . import config, providers, tools
from .models import ModelManager

EFFORT = {   # same relay at every level, only the budgets change
    "low":    dict(think=1024, calls=12, tokens=16384, seconds=180),
    "medium": dict(think=4096, calls=20, tokens=32768, seconds=420),
    "high":   dict(think=8192, calls=30, tokens=65536, seconds=900),
}
# seconds is checked before each call and caps each HTTP wait. loads and tool runs aren't timed, so a request can run over.
ALIASES = {"auto": "medium", "xhigh": "high", "max": "high"}    # old names some clients still send
ANSWER = 4096        # tokens a reply may use after its thinking
CONCLUSION = 2048    # room to ask for just the conclusion when a reply runs out; sized on GPQA replies
SIMPLE_THINK = 1024  # with no thinking at all the 4B guessed the time instead of calling python
# Per-profile relay settings, `relays: {profile: {...}}` in lobes.yaml overrides them.
# checks: per level, what reads a final answer before it ships, in order. "language" is the review model,
# "check" the expert rereading its own draft. a rejected draft is rewritten before the next check; the last
# check's rejection only gets noted.
RELAY = {"think": "effort",       # effort: as the level says | off: never | escalate: only a rewrite thinks
                                  # | first: only the first call of a draft or rewrite thinks
                                  # a model can override this with its own think: in lobes.yaml
         "checks_on": "hard",      # hard: the hard route's answers | all: every answer
         # review: read the finished draft | requirements: read the request first and list what the reply
         # must cover, no review afterwards
         "language": "review",
         # a single check, so a rejection is only noted. rewrites after a rejection broke more answers than they fixed
         "checks": {level: ["language"] for level in EFFORT},
         "recheck": False}         # false: a checker that passed a draft does not read the same draft again


class BudgetExceeded(Exception):
    pass


def effort(cfg):
    level = cfg.get("effort") or "medium"
    level = ALIASES.get(level, level)
    if level not in EFFORT:
        raise ValueError(f"unknown effort {level!r}; use low, medium or high")
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
    route: str = "hard"              # the executive's call: simple | hard
    topic: str | None = None         # the router's call where the profile has experts: the slot that answers
    earlier: str = ""                # the user's previous message, for the router
    now: str = ""                    # local time when the request came in; a client's tool steps keep the first one
    continues: bool = False          # the request carries results of tool calls the client ran for it
    step: str | None = None          # id of the latest tool call in the client's conversation
    cancel: threading.Event = field(default_factory=threading.Event)   # set when the api client goes away
    client_tools: list | None = None # tools the api client runs itself; a call to one ends the request.
                                     # None when it offers none: the request then runs on the local tools
    tool_calls: list = field(default_factory=list)     # those calls, for the client
    on_delta: object = None          # on_delta(kind, text) streams the work; kind is "reasoning", "step" or "content"
    observations: list = field(default_factory=list)   # Observation: what perception and ocr read from images
    requirements: str = ""           # what the review model read the request as asking the reply to satisfy
    tool_results: dict = field(default_factory=dict)   # ref -> raw result dict
    messages: list = field(default_factory=list)       # the reasoning conversation, tool turns included
    ran: list = field(default_factory=list)            # (tool, args, result text) for each call reasoning made
    draft: str | None = None         # reasoning's latest reply
    problems: list = field(default_factory=list)       # what the review sent back
    checked: int = 0                 # how many of the answer's checks have run, across the turn's tool steps
    capped: str | None = None        # which cap ended the request, if one did
    stopped: bool = False            # stop() ended it: the answer says why, after any text already streamed
    uncertainties: list = field(default_factory=list)
    swaps: int = 0
    calls: list = field(default_factory=list)          # (lobe, model, ms, tokens)
    usage: dict = field(default_factory=dict)          # prompt/completion/total tokens summed over calls
    context: dict = field(default_factory=dict)        # usage of reasoning's latest call: how long the conversation is
    answer: str | None = None
    effort: str = "medium"
    t0: float = field(default_factory=time.perf_counter)

    def __post_init__(self):
        # Codex sometimes sends only hosted tools, which responses.py drops. an empty list has to mean "none
        # offered", or the request ends up with no tools at all
        self.client_tools = self.client_tools or None

    def ms(self):
        return int((time.perf_counter() - self.t0) * 1000)

    def summary(self):
        toks = sum(c[3] for c in self.calls)
        return (f"{self.route} calls={len(self.calls)} tools={len(self.tool_results)} sent_back={len(self.problems)} "
                f"swaps={self.swaps} tokens={toks} {self.ms()} ms" + (f" capped={self.capped}" if self.capped else ""))


class Ctx:
    def __init__(self, cfg, profile, trace, rundir):
        self.cfg, self.profile, self.trace, self.rundir = cfg, profile, trace, rundir
        self.level = ALIASES.get(cfg.get("effort"), cfg.get("effort")) or "medium"
        self.effort = effort(cfg)
        self.relay = {**RELAY, **(cfg.get("relays") or {}).get(profile, {})}
        self.workdir = rundir / "work"
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.mm = ModelManager(cfg)

    def slot(self, lobe, state=None):
        """Reasoning is the routed expert once the router has picked one; check is that expert reading its own draft."""
        if lobe == "check":
            lobe = "reasoning"
        if lobe == "reasoning" and state is not None and state.topic:
            lobe = state.topic
        return config.lobe(self.cfg, lobe, self.profile)

    def think_mode(self, state):
        """The relay's think mode, or the routed expert model's own `think` in lobes.yaml."""
        return self.cfg["models"].get(self.slot("reasoning", state)[1], {}).get("think") or self.relay["think"]

    def is_model(self, lobe):
        """True when the profile fills this slot with a model."""
        try:
            prov, _ = self.slot(lobe)
        except KeyError:
            return False
        return prov != "impl"

    def chat(self, state, lobe, messages, *, schema=None, thinking=0, tools=None, temperature=0.2, max_tokens=ANSWER,
             images=None, over=False):
        """thinking is a budget in tokens, 0 for none; max_tokens is what the reply may use on top of it.
        over: the answer a capped request still owes. The caps that ended the request do not apply to this one call."""
        if not over:
            self.check(state)
        prov, model = self.slot(lobe, state)
        mcfg = self.cfg["models"].get(model, {})
        think = int(thinking or 0) if mcfg.get("thinking") else 0
        if not over:
            max_tokens = min(think + max_tokens, self.effort["tokens"] - state.usage.get("completion_tokens", 0))
        if prov == "local":
            n = len(self.mm.events)
            self.mm.ensure(model)
            for name, op, ms, vram in self.mm.events[n:]:
                self.trace.write("model", lobe=lobe, name=name, op=op, ms=ms, vram_mb=vram)
                state.swaps += op == "load"
        seed = self.cfg.get("seed")
        live = None
        if state.on_delta and lobe in ("reasoning", "language", "check"):
            final = lobe == "reasoning" and not checks(self, state)

            def live(kind, text):
                if state.cancel.is_set():       # closes the stream, so llama-server stops generating too
                    raise BudgetExceeded("cancelled")
                if kind == "tool_call":         # not shown, only here so a cancel also stops a long tool call
                    return
                if kind == "reasoning" and not think:   # llama-server sends a prefilled thought back; it showed when written
                    return
                if kind == "reasoning" or lobe != "reasoning":
                    state.on_delta("reasoning", text)
                elif final:
                    state.on_delta("content", text)
                # a hard draft's text waits for its review and goes out once, as the answer
        if not over:
            self.check(state)
        try:
            r = providers.chat(self.cfg["providers"][prov], model, messages, schema=schema, images=images,
                               thinking=bool(think), thinking_budget=min(think, max_tokens // 2) if think else None,
                               tools=tools, temperature=temperature, max_tokens=max_tokens,
                               seed=None if seed is None else seed + len(state.calls),
                               timeout=self.effort["seconds"] if over else max(0.1, self.effort["seconds"] - state.ms() / 1000),
                               ctx=mcfg.get("ctx") or self.cfg.get("llama", {}).get("ctx"), on_delta=live)
        except httpx.TimeoutException as exc:
            state.capped = "seconds"
            self.trace.write("cap", limit=state.capped, lobe=lobe)
            raise BudgetExceeded(state.capped) from exc
        toks = r.usage.get("total_tokens", 0)
        for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
            state.usage[k] = state.usage.get(k, 0) + r.usage.get(k, 0)
        if lobe == "reasoning" and not state.context:   # later calls add a repair or internal tool turns the client never sees
            state.context = r.usage
        state.calls.append((lobe, f"{prov}/{model}", r.ms, toks))
        self.trace.write("call", lobe=lobe, model=f"{prov}/{model}", ms=r.ms, tokens=toks, thinking=think,
                         max_tokens=max_tokens, usage=r.usage, temperature=temperature,
                         parsed=r.data is not None if schema else None, finish=r.finish,
                         text=r.text[:4000], tool_calls=r.tool_calls, reasoning=(r.reasoning or "")[:2000], timings=r.timings)
        return r

    def check(self, state):
        if state.cancel.is_set():
            raise BudgetExceeded("cancelled")
        if capped(self, state):
            raise BudgetExceeded(state.capped)


def reads_request(ctx, state):
    """-> whether the review model reads this request before the draft instead of the draft after it."""
    return (ctx.relay["language"] == "requirements" and ctx.is_model("language") and not state.requirements
            and (ctx.relay["checks_on"] != "hard" or state.route == "hard"))


def checks(ctx, state):
    """-> the checks this request's final text answer goes through, in order."""
    if ctx.relay["checks_on"] == "hard" and state.route != "hard":
        return []
    todo = ctx.relay["checks"].get(ctx.level, [])
    if ctx.relay["language"] == "requirements":  # it already had its call, before the draft
        todo = [c for c in todo if c != "language"]
    return [c for c in todo if ctx.is_model(c)]


def capped(ctx, state):
    e = ctx.effort
    if state.capped:
        return state.capped
    if len(state.calls) >= e["calls"]:
        state.capped = "calls"
    elif state.usage.get("completion_tokens", 0) >= e["tokens"]:
        state.capped = "tokens"
    elif state.ms() >= e["seconds"] * 1000:
        state.capped = "seconds"
    return state.capped


def run_tool(ctx, state, name, args):
    """Runs one call. -> the result as the text the model reads next; the raw result goes to the run dir.
    Tools have their own timeout; the request caps only limit new model calls."""
    ref = f"tool_{len(state.tool_results)}"
    res = tools.run(name, args, ctx.workdir)
    (ctx.rundir / f"{ref}.json").write_text(json.dumps({"call": {"name": name, "args": args}, "result": res},
                                                       ensure_ascii=False, indent=1), encoding="utf-8")
    state.tool_results[ref] = res
    out = (res.get("stdout") or res.get("content") or "").strip()
    ctx.trace.write("tool", ref=ref, call={"name": name, "args": args}, exit=res.get("exit"), out=out[:500],
                    stderr=(res.get("stderr") or "")[-300:])
    if res.get("exit") != 0:
        out += f"\n(exit {res.get('exit')}) {(res.get('stderr') or '').strip()}"
    if res.get("image") and ctx.is_model("perception"):
        from .lobe import perception
        seen = len(state.observations)
        perception.look(ctx, state, images=[res["image"]])
        out += "".join(f"\n{o.summary}" for o in state.observations[seen:])
    return out.strip() or "(no output)"


def arguments(call):
    """The arguments of an openai tool call as a dict, or None when the model wrote something else."""
    a = (call.get("function") or {}).get("arguments") or "{}"
    try:
        a = a if isinstance(a, dict) else json.loads(a)
    except json.JSONDecodeError:
        return None
    return a if isinstance(a, dict) else None


def call_tools(ctx, state, r, messages, specs, extra=None):
    """Appends the assistant turn and one tool turn per call. extra maps a tool name to a handler that takes the
    call instead of tools.run. -> [(name, args, result text)]"""
    turn = {"role": "assistant", "content": r.text, "tool_calls": r.tool_calls}
    if r.reasoning:
        turn["reasoning_content"] = r.reasoning
    messages.append(turn)
    names = {s["function"]["name"] for s in specs}
    done = []
    for call in r.tool_calls:
        if state.cancel.is_set():
            raise BudgetExceeded("cancelled")
        name, args = (call.get("function") or {}).get("name", ""), arguments(call)
        if name not in names:
            text = f"There is no tool named {name}."
        elif args is None:
            text = "The arguments were not a JSON object."
        elif name in (extra or {}):
            text = extra[name](args)
        else:
            text = run_tool(ctx, state, name, args)
        messages.append({"role": "tool", "tool_call_id": call.get("id", ""), "content": text})
        say(state, f"\n[{name}] {json.dumps(args, ensure_ascii=False)[:1000]}\n{text[-2000:]}\n")
        done.append((name, args or {}, text))
    return done


def stop(state, zh, en):
    """Ends the request with this answer, in the request's language. A rewrite that stops keeps the reviewed draft."""
    text = zh if re.search(r"[一-鿿]", state.goal) else en
    if state.draft:     # a repair: the draft ships as when the last check rejects it, with why the rewrite stopped
        state.uncertainties += [f"A review still found a problem: {p}" for p in state.problems[-1:]] + [text.splitlines()[0]]
    else:
        state.answer, state.stopped = text, True
    raise BudgetExceeded(en.splitlines()[0])


def stop_repeating(state):
    name, _, out = state.ran[-1]
    stop(state, f"{name} 三次返回同样的结果，已停下，没有再发同样的调用：\n\n{out[-3000:]}",
         f"{name} returned the same result three times, so I stopped instead of sending it again:\n\n{out[-3000:]}")


def say(state, text):
    """Shows a step of the relay to a streaming client. Chat completions sends it as reasoning; responses.py makes it
    the status label and the first line of the thinking after it."""
    if state.on_delta:
        state.on_delta("step", text)


NOTICES = ("This is an automatically generated checkpoint", "The approval policy changed",
           "You are repeating the exact same tool call",      # user messages DeepSeek Harness writes itself
           "Another language model started to solve this problem", "<environment_context>", "<turn_aborted>",
           "# AGENTS.md instructions for")      # and the ones Codex writes


def typed(m):
    """True for a user message a person wrote, false for a harness notice."""
    c = m.get("content")
    text = c if isinstance(c, str) else "".join(p.get("text", "") for p in c or [] if isinstance(p, dict))
    return m["role"] == "user" and not text.startswith(NOTICES)


def request_at(messages):
    """-> index of the user message the current turn answers, None without one. A turn ends with an assistant message
    that calls no tools, or when the user types after a tool step that never got its reply (stopped, or it failed).
    Notices a harness adds while a turn runs belong to that turn; a checkpoint is the request when it replaced it."""
    start = 1 + max((i for i, m in enumerate(messages) if m["role"] == "assistant" and not m.get("tool_calls")), default=-1)
    first = next((i for i in range(start, len(messages)) if messages[i]["role"] == "assistant"), len(messages))
    start = max((i for i in range(first, len(messages)) if typed(messages[i])), default=start)
    working = next((i for i in range(start, len(messages)) if messages[i]["role"] == "assistant"), len(messages))
    users = ([i for i in range(start, working) if messages[i]["role"] == "user"]
             or [i for i, m in enumerate(messages) if m["role"] == "user"])
    asks = [i for i in users if typed(messages[i])] or users
    return asks[-1] if asks else None


def ran_in_turn(messages):
    """-> [(tool, args, result text)] for the client's tool calls since the turn's request, for the review."""
    last = request_at(messages)
    last = -1 if last is None else last
    results = {m.get("tool_call_id"): m.get("content") or "" for m in messages[last + 1:] if m["role"] == "tool"}
    return [((c.get("function") or {}).get("name", ""), arguments(c) or {}, results.get(c.get("id"), ""))
            for m in messages[last + 1:] if m["role"] == "assistant" for c in m.get("tool_calls") or []]


def run(cfg, goal, *, profile=None, images=None, task_id=None, messages=None, client_tools=None, on_delta=None,
        cancel=None):
    """messages: the whole conversation from an api client, ending with the request or with results of client_tools.
    Without it reasoning starts a conversation from the goal. cancel: a threading.Event that stops the request."""
    from .lobe import executive, language, perception, reasoning

    task_id = task_id or time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    rundir = cfg["_root"] / "runs" / task_id
    trace = Trace(rundir / "trace.jsonl")
    ctx = Ctx(cfg, profile or cfg["profile"], trace, rundir)
    state = TaskState(task_id, goal, [str(p) for p in images or []], client_tools=client_tools, on_delta=on_delta)
    state.effort = ctx.level
    if cancel is not None:
        state.cancel = cancel
    if messages:
        at = request_at(messages)
        prev = request_at(messages[:at])
        state.earlier = messages[prev]["content"] if prev is not None else ""
        state.continues = any(m["role"] == "assistant" for m in messages[at + 1:])
        state.step = next((c.get("id") for m in reversed(messages) if m["role"] == "assistant"
                           for c in m.get("tool_calls") or []), None)
        state.ran = ran_in_turn(messages)
    trace.write("start", goal=goal, profile=ctx.profile, images=state.images, effort=cfg.get("effort") or "medium")
    local = [spec.split("/", 1)[1] for spec in cfg["profiles"][ctx.profile].values() if spec.startswith("local/")]
    if local:
        ctx.mm.validate(local)

    try:
        executive.intake(ctx, state)
        trace.write("intake", route=state.route, topic=state.topic)
        say(state, f"[lobes] {state.route}, {'/'.join(ctx.slot('reasoning', state))}"
                   + (f" for {state.topic}" if state.topic else "") + "\n")
        if state.images and ctx.is_model("perception"):
            perception.look(ctx, state)
        if reads_request(ctx, state):
            try:
                state.requirements = language.requirements(ctx, state)
                trace.write("requirements", text=state.requirements[:2000])
            except BudgetExceeded:
                raise
            except Exception as e:  # optional like the review; on failure the request goes as written
                trace.write("language_error", error=str(e)[:300])
        if messages:
            state.messages = [dict(m) for m in messages]
            state.messages[request_at(messages)]["content"] = reasoning.brief(state)
        think = SIMPLE_THINK if state.route == "simple" else ctx.effort["think"]
        mode = ctx.think_mode(state)
        if mode in ("off", False) or (mode == "escalate" and not state.problems):  # yaml reads off as false
            think = 0
        if mode == "first" and state.continues:    # a client's tool result: the step's plan already thought
            think = 0
        if len(state.ran) > 2 and state.ran[-1] == state.ran[-2] == state.ran[-3]:
            # thinking after the second repeat did not change the call either: show the result instead of sending it again
            stop_repeating(state)
        if len(state.ran) > 1 and state.ran[-1] == state.ran[-2]:
            # the last call repeated the one before and got the same result: the fast step is not reading it
            think = ctx.effort["think"]
            trace.write("stalled", tool=state.ran[-1][0])
        state.draft = reasoning.solve(ctx, state, think)
        if not state.tool_calls and checks(ctx, state):
            try:
                _review(ctx, state, think)
            except BudgetExceeded:
                raise
            except Exception as e:      # the review is optional: a model that fails to load or a bad reply keeps the draft
                trace.write("language_error", error=str(e)[:300])
    except BudgetExceeded as exc:
        trace.write("stop", reason=str(exc))
        if state.capped and not state.draft and not state.stopped and not state.tool_calls:
            try:
                state.draft = reasoning.answer_now(ctx, state)
            except Exception as e:      # best effort, otherwise the reply is just "I couldn't produce an answer"
                trace.write("stop", reason=f"no last word: {e}"[:300])
    finally:
        tools.close(ctx.workdir)
    if state.tool_calls:
        executive.remember(ctx, state)
    return _finish(ctx, state)


def _review(ctx, state, think):
    """Runs the draft through its checks in order. A rejected draft is rewritten before the next check, and the last
    check's rejection is noted instead. Checks from an earlier step of the turn count: a rewrite that called a client
    tool comes back as a new request."""
    from .lobe import language, reasoning
    todo, passed = checks(ctx, state), set()        # passed: checkers that already passed the current draft
    if ctx.think_mode(state) in ("escalate", "first"):
        think = ctx.effort["think"]
    while state.checked < len(todo):
        if not state.draft:
            return
        who = todo[state.checked]
        if state.problems and state.checked == len(todo) - 1 and who in todo[:-1]:
            break       # the checker that sent it back would only add a note: the repaired draft ships either way
        state.checked += 1
        if who in passed and not ctx.relay["recheck"]:
            continue
        say(state, "\n[self-check] " if who == "check" else "\n[review] ")
        ok, text = language.review(ctx, state, who)
        ctx.trace.write("review", by=who, ok=ok, text=text[:2000])
        if ok:
            passed.add(who)
            continue
        state.problems.append(text)
        if state.checked == len(todo):
            state.uncertainties.append(f"A review still found a problem: {text}")
            return
        passed.clear()
        say(state, "\n[repair]\n")
        state.draft = reasoning.solve(ctx, state, think, feedback=text)
        if state.tool_calls:
            return
    state.answer = state.draft


def _finish(ctx, state):
    if state.capped:
        state.uncertainties.append(f"Stopped at the request's {state.capped} limit.")
    if not state.answer:
        state.answer = (state.draft or "").strip() or ("" if state.tool_calls else
            "未能生成可用的回答。" if re.search(r"[一-鿿]", state.goal) else "I couldn't produce an answer.")
    for note in state.uncertainties:
        say(state, f"\n[note] {note}\n")
    ctx.trace.write("final", answer=state.answer, uncertainties=state.uncertainties, summary=state.summary())
    return state
