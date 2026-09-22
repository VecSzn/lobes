"""Reasoning works the request out in plain text with native tool calls. It runs python itself; files, the shell,
the web and the screen it asks the motor lobe for in words."""
import dataclasses

from .. import tools
from ..task import ANSWER, CONCLUSION, say, stop, stop_repeating
from . import motor

MOTOR = tools.spec("motor", "Ask the tool hand to read or write files in the work dir, run shell commands, search the "
                   "web, read a page or take a screenshot. Say in plain words what you need; you get the raw results "
                   "back.",
                   {"request": {"type": "string"}})


def brief(state):
    """The request, and for images what the perception lobe and the ocr engine read, each with its source."""
    lines = [state.goal]
    if state.work.observations:
        lines.append("\nWhat was read from the attached images:")
    lines += [f"[{o.ref}] ({o.source}) {o.summary}" for o in state.work.observations]
    if state.turn.requirements:     # labelled as another lobe's reading, so the request above still wins
        lines.append(f"\nWhat another lobe read the request as asking of the reply:\n{state.turn.requirements}")
    if state.turn.now:              # without it the model invents a date. one stamp per turn so tool steps stay cached
        lines.append(f"\n(Local time: {state.turn.now})")
    return "\n".join(lines)


def solve(ctx, state, think, feedback=None):
    """-> reasoning's reply. The conversation stays in state.work.messages, so a repair continues it."""
    work = state.work
    if state.client_tools is not None:      # an api client that runs its own tools
        specs, extra = state.client_tools, None
    elif ctx.is_model("motor"):
        specs = tools.specs(["python"]) + [MOTOR]
        extra = {"motor": lambda args: motor.act(ctx, state, str(args.get("request", "")))}
    else:                       # no tool hand in this profile: reasoning holds every tool
        specs, extra = tools.specs(), None
    if ctx.cfg["models"].get(ctx.slot("reasoning", state)[1], {}).get("tools") is False:
        specs = []              # its calls come back as plain text that nothing runs
    if not work.messages:
        work.messages.append({"role": "user", "content": brief(state)})
    if feedback:
        work.messages.append({"role": "user", "content": "A reviewer checked your answer and found a problem: "
                                                         f"{feedback}\nFix it and write your whole answer again."})
    cut = False
    while True:                 # the request's call cap ends a model that never stops calling tools
        room = ANSWER * (2 if cut else 1)
        r = ctx.chat(state, "reasoning", work.messages, thinking=think, tools=specs or None, temperature=0.6, max_tokens=room)
        if r.finish == "length" and any(tools.arguments(c) is None for c in r.tool_calls):
            # ran out of tokens while typing a call's arguments by hand, e.g. a huge number into write_file.
            # don't run it or pass it on. retry once with thinking, which tends to compute the value instead, and
            # twice the room; a second cut stops the request.
            names = [(c.get("function") or {}).get("name", "") for c in r.tool_calls]
            ctx.trace.write("cut", tools=names)
            if cut:
                stop(state, f"{', '.join(names)} 的调用连续两次写到 token 上限还没写完，已停下。",
                     f"The {', '.join(names)} call reached the token limit twice before its arguments were complete, so I stopped.")
            cut = True
            work.messages.append({"role": "assistant", "content": r.text, "tool_calls": r.tool_calls})
            work.messages += [{"role": "tool", "tool_call_id": c.get("id", ""), "content": "Not run." if tools.arguments(c) is not None
                               else "The reply reached its token limit before this call's arguments were complete. Nothing ran."}
                              for c in r.tool_calls]
            think = max(think, ctx.effort["think"])
            say(state, "\n[retry] a tool call was cut off at the token limit\n")    # the retry's text starts a new message
            continue
        if r.finish == "length" and not r.tool_calls and r.text.strip():
            # out of room before the last line. the work is there, so ask for just the conclusion
            ask = work.messages + [{"role": "assistant", "content": r.text},
                                   {"role": "user", "content": "That reply reached the token limit before you "
                                    "finished. State your conclusion now, on its own. Do not work through it again."}]
            end = ctx.chat(state, "reasoning", ask, thinking=0, temperature=0.6, max_tokens=CONCLUSION)
            # appended, since readers take the last block as the answer. if the conclusion got cut too, drop it
            # so it doesn't bury whatever the draft did reach
            if end.finish != "length" and end.text.strip():
                r = dataclasses.replace(r, text=r.text.rstrip() + "\n\n" + end.text.strip())
        cut = False
        if not r.tool_calls:
            work.messages.append({"role": "assistant", "content": r.text})
            return r.text.strip()
        if state.client_tools is not None:  # the client runs them and asks again with the results
            state.tool_calls = r.tool_calls
            return r.text.strip()
        work.ran += motor.call_tools(ctx, state, r, work.messages, specs, extra)
        if ctx.think_mode(state) == "first":    # the plan is made; the steps after a tool result go without thinking
            think = 0
        # same repeat rule runner.run uses for client calls, but over the whole request, since a model can loop on
        # a failing call. re-asking without tools doesn't help, the call comes back as text and ships as the answer
        if work.ran.count(work.ran[-1]) > 2:
            stop_repeating(state)
        elif work.ran[-1] in work.ran[:-1]:
            think = max(think, ctx.effort["think"])
            ctx.trace.write("stalled", tool=work.ran[-1][0])


def answer_now(ctx, state):
    """-> the answer a capped request still owes, from the conversation it already has. One call, no tools.
    A capped request has usually done most of the work, so it gets one last turn to answer from it."""
    msgs = list(state.work.messages)
    at = max((i for i, m in enumerate(msgs) if m.get("tool_calls")), default=None)
    if at is not None:      # a cap can land between a call and its result, and no server accepts that conversation
        have = {m.get("tool_call_id") for m in msgs[at + 1:] if m.get("role") == "tool"}
        msgs += [{"role": "tool", "tool_call_id": c.get("id", ""), "content": "Not run: the request ran out of budget."}
                 for c in msgs[at]["tool_calls"] if c.get("id") not in have]
    ask = msgs + [{"role": "user", "content": "This request has reached its budget, so this is your last turn and no "
                                              "tool will run. Answer it now from what you have, and say what you are "
                                              "unsure of."}]
    return ctx.chat(state, "reasoning", ask, thinking=0, temperature=0.6,
                    max_tokens=CONCLUSION, over=True).text.strip()
