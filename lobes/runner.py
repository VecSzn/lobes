"""Relay a request through small specialists as plain text, under one shared request budget."""
import dataclasses
import json
import re
import time
import uuid

import httpx

from . import config, providers, tools
from .lobe import executive, language, perception, reasoning
from .models import ModelManager
from .task import ALIASES, ANSWER, EFFORT, SIMPLE_THINK, BudgetExceeded, TaskState, effort, say, stop_repeating

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


class Trace:
    def __init__(self, path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, kind, **fields):
        rec = {"t": round(time.time(), 3), "kind": kind, **fields}
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")


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
        if lobe == "reasoning" and state is not None and state.turn.topic:
            lobe = state.turn.topic
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

    def chat(self, state, lobe, messages, *, schema=None, choices=None, thinking=0, tools=None, temperature=0.2,
             max_tokens=ANSWER, images=None, over=False):
        """thinking is a budget in tokens, 0 for none; max_tokens is what the reply may use on top of it.
        over: the answer a capped request still owes. The caps that ended the request do not apply to this one call.
        Every lobe gets back an answer or a tool call. A reply that ended inside its thinking is asked once more with
        the thought passed back, or the model just redoes the same failing call; if it still says nothing, the
        thought is the answer."""
        ask = dict(schema=schema, choices=choices, tools=tools, temperature=temperature, max_tokens=max_tokens,
                   images=images, over=over)
        r = self._call(state, lobe, messages, thinking=thinking, **ask)
        if r.tool_calls or r.text.strip() or not r.reasoning:
            return r
        thought = r.reasoning.strip().removesuffix(providers.BUDGET_MESSAGE.strip()).rstrip()
        self.trace.write("thought_only", lobe=lobe)
        r = self._call(state, lobe, messages + [{"role": "assistant", "content": "", "reasoning_content": thought}],
                       thinking=0, **ask)
        return r if r.tool_calls or r.text.strip() else dataclasses.replace(r, text=thought)

    def _call(self, state, lobe, messages, *, schema, choices, thinking, tools, temperature, max_tokens, images, over):
        spend = state.spend
        if not over:
            self.check(state)
        prov, model = self.slot(lobe, state)
        mcfg = self.cfg["models"].get(model, {})
        think = int(thinking or 0) if mcfg.get("thinking") else 0
        if not over:
            max_tokens = min(think + max_tokens, self.effort["tokens"] - spend.usage.get("completion_tokens", 0))
        if prov == "local":
            n = len(self.mm.events)
            self.mm.ensure(model)
            for name, op, ms, vram in self.mm.events[n:]:
                self.trace.write("model", lobe=lobe, name=name, op=op, ms=ms, vram_mb=vram)
                spend.swaps += op == "load"
        # Counted per lobe, a classifier call before the solver doesn't change the solver's seed
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
            r = providers.chat(self.cfg["providers"][prov], model, messages, schema=schema, choices=choices, images=images,
                               thinking=bool(think), thinking_budget=min(think, max_tokens // 2) if think else None,
                               tools=tools, temperature=temperature, max_tokens=max_tokens,
                               seed=None if seed is None else seed + sum(c[0] == lobe for c in spend.calls),
                               timeout=self.effort["seconds"] if over else max(0.1, self.effort["seconds"] - spend.ms() / 1000),
                               ctx=mcfg.get("ctx") or self.cfg.get("llama", {}).get("ctx"), on_delta=live)
        except httpx.TimeoutException as exc:
            spend.capped = "seconds"
            self.trace.write("cap", limit=spend.capped, lobe=lobe)
            raise BudgetExceeded(spend.capped) from exc
        toks = r.usage.get("total_tokens", 0)
        for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
            spend.usage[k] = spend.usage.get(k, 0) + r.usage.get(k, 0)
        if lobe == "reasoning" and not spend.context:   # later calls add a repair or internal tool turns the client never sees
            spend.context = r.usage
        spend.calls.append((lobe, f"{prov}/{model}", r.ms, toks))
        self.trace.write("call", lobe=lobe, model=f"{prov}/{model}", ms=r.ms, tokens=toks, thinking=think,
                         max_tokens=max_tokens, usage=r.usage, temperature=temperature,
                         parsed=r.data is not None if schema or choices else None, finish=r.finish,
                         text=r.text[:4000], tool_calls=r.tool_calls, reasoning=(r.reasoning or "")[:2000], timings=r.timings)
        return r

    def check(self, state):
        if state.cancel.is_set():
            raise BudgetExceeded("cancelled")
        if state.spend.over(self.effort):
            raise BudgetExceeded(state.spend.capped)


def reads_request(ctx, state):
    """-> whether the review model reads this request before the draft instead of the draft after it."""
    return (ctx.relay["language"] == "requirements" and ctx.is_model("language") and not state.turn.requirements
            and (ctx.relay["checks_on"] != "hard" or state.turn.route == "hard"))


def checks(ctx, state):
    """-> the checks this request's final text answer goes through, in order."""
    if ctx.relay["checks_on"] == "hard" and state.turn.route != "hard":
        return []
    todo = ctx.relay["checks"].get(ctx.level, [])
    if ctx.relay["language"] == "requirements":  # it already had its call, before the draft
        todo = [c for c in todo if c != "language"]
    return [c for c in todo if ctx.is_model(c)]


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
    return [((c.get("function") or {}).get("name", ""), tools.arguments(c) or {}, results.get(c.get("id"), ""))
            for m in messages[last + 1:] if m["role"] == "assistant" for c in m.get("tool_calls") or []]


def run(cfg, goal, *, profile=None, images=None, task_id=None, messages=None, client_tools=None, on_delta=None,
        cancel=None):
    """messages: the whole conversation from an api client, ending with the request or with results of client_tools.
    Without it reasoning starts a conversation from the goal. cancel: a threading.Event that stops the request."""
    task_id = task_id or time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    rundir = cfg["_root"] / "runs" / task_id
    trace = Trace(rundir / "trace.jsonl")
    ctx = Ctx(cfg, profile or cfg["profile"], trace, rundir)
    state = TaskState(task_id, goal, [str(p) for p in images or []], client_tools=client_tools, on_delta=on_delta,
                      effort=ctx.level)
    if cancel is not None:
        state.cancel = cancel
    if messages:
        at = request_at(messages)
        prev = request_at(messages[:at])
        state.earlier = messages[prev]["content"] if prev is not None else ""
        state.continues = any(m["role"] == "assistant" for m in messages[at + 1:])
        state.step = next((c.get("id") for m in reversed(messages) if m["role"] == "assistant"
                           for c in m.get("tool_calls") or []), None)
        state.work.ran = ran_in_turn(messages)
    trace.write("start", goal=goal, profile=ctx.profile, images=state.images, effort=cfg.get("effort") or "medium")
    local = [spec.split("/", 1)[1] for spec in cfg["profiles"][ctx.profile].values() if spec.startswith("local/")]
    if local:
        ctx.mm.validate(local)

    try:
        executive.intake(ctx, state)
        turn, work = state.turn, state.work
        trace.write("intake", route=turn.route, topic=turn.topic)
        say(state, f"[lobes] {turn.route}, {'/'.join(ctx.slot('reasoning', state))}"
                   + (f" for {turn.topic}" if turn.topic else "") + "\n")
        if state.images and ctx.is_model("perception"):
            perception.look(ctx, state)
        if reads_request(ctx, state):
            try:
                turn.requirements = language.requirements(ctx, state)
                trace.write("requirements", text=turn.requirements[:2000])
            except BudgetExceeded:
                raise
            except Exception as e:  # optional like the review; on failure the request goes as written
                trace.write("language_error", error=str(e)[:300])
        if messages:
            work.messages = [dict(m) for m in messages]
            work.messages[request_at(messages)]["content"] = reasoning.brief(state)
        think = SIMPLE_THINK if turn.route == "simple" else ctx.effort["think"]
        mode = ctx.think_mode(state)
        if mode in ("off", False) or (mode == "escalate" and not turn.problems):  # yaml reads off as false
            think = 0
        if mode == "first" and state.continues:    # a client's tool result: the step's plan already thought
            think = 0
        if len(work.ran) > 2 and work.ran[-1] == work.ran[-2] == work.ran[-3]:
            # thinking after the second repeat did not change the call either: show the result instead of sending it again
            stop_repeating(state)
        if len(work.ran) > 1 and work.ran[-1] == work.ran[-2]:
            # the last call repeated the one before and got the same result: the fast step is not reading it
            think = ctx.effort["think"]
            trace.write("stalled", tool=work.ran[-1][0])
        work.draft = reasoning.solve(ctx, state, think)
        if not state.tool_calls and checks(ctx, state):
            try:
                _review(ctx, state, think)
            except BudgetExceeded:
                raise
            except Exception as e:      # the review is optional: a model that fails to load or a bad reply keeps the draft
                trace.write("language_error", error=str(e)[:300])
    except BudgetExceeded as exc:
        trace.write("stop", reason=str(exc))
        if state.spend.capped and not state.work.draft and not state.stopped and not state.tool_calls:
            try:
                state.work.draft = reasoning.answer_now(ctx, state)
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
    turn, work = state.turn, state.work
    todo, passed = checks(ctx, state), set()        # passed: checkers that already passed the current draft
    if ctx.think_mode(state) in ("escalate", "first"):
        think = ctx.effort["think"]
    while turn.checked < len(todo):
        if not work.draft:
            return
        who = todo[turn.checked]
        if turn.problems and turn.checked == len(todo) - 1 and who in todo[:-1]:
            break       # the checker that sent it back would only add a note: the repaired draft ships either way
        turn.checked += 1
        if who in passed and not ctx.relay["recheck"]:
            continue
        say(state, "\n[self-check] " if who == "check" else "\n[review] ")
        ok, text = language.review(ctx, state, who)
        ctx.trace.write("review", by=who, ok=ok, text=text[:2000])
        if ok:
            passed.add(who)
            continue
        turn.problems.append(text)
        if turn.checked == len(todo):
            state.uncertainties.append(f"A review still found a problem: {text}")
            return
        passed.clear()
        say(state, "\n[repair]\n")
        work.draft = reasoning.solve(ctx, state, think, feedback=text)
        if state.tool_calls:
            return
    state.answer = work.draft


def _finish(ctx, state):
    if state.spend.capped:
        state.uncertainties.append(f"Stopped at the request's {state.spend.capped} limit.")
    if not state.answer:
        state.answer = (state.work.draft or "").strip() or ("" if state.tool_calls else
            "未能生成可用的回答。" if re.search(r"[一-鿿]", state.goal) else "I couldn't produce an answer.")
    for note in state.uncertainties:
        say(state, f"\n[note] {note}\n")
    ctx.trace.write("final", answer=state.answer, uncertainties=state.uncertainties, summary=state.summary())
    return state
