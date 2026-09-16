"""Reasoning works the request out in plain text with native tool calls. It runs python itself; files, the shell,
the web and the screen it asks the motor lobe for in words."""
import dataclasses

from .. import providers, tools

MOTOR = tools.spec("motor", "Ask the tool hand to read or write files in the work dir, run shell commands, search the "
                   "web, read a page or take a screenshot. Say in plain words what you need; you get the raw results "
                   "back.",
                   {"request": {"type": "string"}})


def brief(state):
    """The request, and for images what the perception lobe and the ocr engine read, each with its source."""
    lines = [state.goal]
    if state.observations:
        lines.append("\nWhat was read from the attached images:")
    lines += [f"[{o.ref}] ({o.source}) {o.summary}" for o in state.observations]
    if state.requirements:      # named for what it is, like the images are: the request above is what counts
        lines.append(f"\nWhat another lobe read the request as asking of the reply:\n{state.requirements}")
    if state.now:               # asked the date, qwen made one up; one stamp per turn keeps the tool steps cached
        lines.append(f"\n(Local time: {state.now})")
    return "\n".join(lines)


def solve(ctx, state, think, feedback=None):
    """-> reasoning's reply. The conversation stays in state.messages, so a repair continues it."""
    from ..runner import ANSWER, CONCLUSION, arguments, call_tools, say, stop, stop_repeating
    from . import motor
    if state.client_tools is not None:      # an api client that runs its own tools
        specs, extra = state.client_tools, None
    elif ctx.is_model("motor"):
        specs = tools.specs(["python"]) + [MOTOR]
        extra = {"motor": lambda args: motor.act(ctx, state, str(args.get("request", "")))}
    else:                       # no tool hand in this profile: reasoning holds every tool
        specs, extra = tools.specs(), None
    if ctx.cfg["models"].get(ctx.slot("reasoning", state)[1], {}).get("tools") is False:
        specs = []              # its calls come back as plain text that nothing runs
    if not state.messages:
        state.messages.append({"role": "user", "content": brief(state)})
    if feedback:
        state.messages.append({"role": "user", "content": "A reviewer checked your answer and found a problem: "
                                                          f"{feedback}\nFix it and write your whole answer again."})
    cut = False
    while True:                 # the request's call cap ends a model that never stops calling tools
        room = ANSWER * (2 if cut else 1)
        r = ctx.chat(state, "reasoning", state.messages, thinking=think, tools=specs or None, temperature=0.6, max_tokens=room)
        if not r.tool_calls and not r.text.strip() and r.reasoning:     # qwen can end inside its thinking, answer and all
            thought = r.reasoning.strip().removesuffix(providers.BUDGET_MESSAGE.strip()).rstrip()
            # asked again without it, qwen3.5-4b ran the same failing call 30 times; with it, it writes out what it concluded
            r = ctx.chat(state, "reasoning", state.messages + [{"role": "assistant", "content": "", "reasoning_content": thought}],
                         thinking=0, tools=specs or None, temperature=0.6, max_tokens=room)
            if not r.tool_calls and not r.text.strip():     # it stopped at once: the thought was the whole answer
                r = dataclasses.replace(r, text=thought)
        if r.finish == "length" and any(arguments(c) is None for c in r.tool_calls):
            # cut off while typing a call's data by hand (multi-43: digits of 3**500 into write_file): nothing runs,
            # the client never gets the broken call, and the retry thinks, which is when the 4B computes instead.
            # The retry has twice the room for a long file; cut again, the call does not fit and the request stops.
            names = [(c.get("function") or {}).get("name", "") for c in r.tool_calls]
            ctx.trace.write("cut", tools=names)
            if cut:
                stop(state, f"{', '.join(names)} 的调用连续两次写到 token 上限还没写完，已停下。",
                     f"The {', '.join(names)} call reached the token limit twice before its arguments were complete, so I stopped.")
            cut = True
            state.messages.append({"role": "assistant", "content": r.text, "tool_calls": r.tool_calls})
            state.messages += [{"role": "tool", "tool_call_id": c.get("id", ""), "content": "Not run." if arguments(c) is not None
                                else "The reply reached its token limit before this call's arguments were complete. Nothing ran."}
                               for c in r.tool_calls]
            think = max(think, ctx.effort["think"])
            say(state, "\n[retry] a tool call was cut off at the token limit\n")    # the retry's text starts a new message
            continue
        if r.finish == "length" and not r.tool_calls and r.text.strip():
            # Out of room mid-sentence with the work already written: the draft scores zero for want of its last
            # line, so it is asked for that line alone.
            ask = state.messages + [{"role": "assistant", "content": r.text},
                                    {"role": "user", "content": "That reply reached the token limit before you "
                                     "finished. State your conclusion now, on its own. Do not work through it again."}]
            end = ctx.chat(state, "reasoning", ask, thinking=0, temperature=0.6, max_tokens=CONCLUSION)
            # it goes after the draft, because a reader takes the last block for the answer. That is also why a
            # conclusion cut off in its turn is dropped: it would bury whatever the draft did reach (GPQA 09-18,
            # 23 items: keeping the cut ones scored 1 and kept 19 without an answer, against 7 and 13 for the draft).
            if end.finish != "length" and end.text.strip():
                r = dataclasses.replace(r, text=r.text.rstrip() + "\n\n" + end.text.strip())
        cut = False
        if not r.tool_calls:
            state.messages.append({"role": "assistant", "content": r.text})
            return r.text.strip()
        if state.client_tools is not None:  # the client runs them and asks again with the results
            state.tool_calls = r.tool_calls
            return r.text.strip()
        state.ran += call_tools(ctx, state, r, state.messages, specs, extra)
        if ctx.think_mode(state) == "first":    # the plan is made; the steps after a tool result go without thinking
            think = 0
        # the rule runner.run keeps for client calls, over the whole request: NeoHorse sent one 404 fetch 18 times
        # (HumanEval-30, 09-17), and nemotron sent two failing snippets in turn up to the call cap (tools-64, 09-17).
        # Asked again without tools, qwen writes the call as text and that would ship as the answer.
        if state.ran.count(state.ran[-1]) > 2:
            stop_repeating(state)
        elif state.ran[-1] in state.ran[:-1]:
            think = max(think, ctx.effort["think"])
            ctx.trace.write("stalled", tool=state.ran[-1][0])


def answer_now(ctx, state):
    """-> the answer a capped request still owes, from the conversation it already has. One call, no tools.

    A request that ran out of calls has read files and worked most of it out; shipping "I couldn't produce an
    answer" throws that away, and the reader gets nothing they can use or correct.
    """
    from ..runner import CONCLUSION
    msgs = list(state.messages)
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
