"""Motor: the tool hand. Reasoning says in words what it needs from files, the shell, the web or the screen; motor
makes the tool calls, reads the results and answers from them. Running a tool call goes through here for every lobe.
The raw results stay here. Passed through, a `cat` of a file would sit in the solver's conversation and get
prefilled again on every later turn."""
import json

from .. import tools
from ..task import BudgetExceeded, say
from . import perception

SYS = ("You are the tool hand. Carry out the request with tool calls; relative paths are in the work dir. Then "
       "answer the request from what they returned. Whoever asked cannot see the results, so your reply has to "
       "carry what they need out of them, including anything that failed or came back empty.")
HAND = [name for name in tools.TOOLS if name != "python"]     # python is reasoning's own tool: it writes the programs


def run_tool(ctx, state, name, args):
    """Runs one call. -> the result as the text the model reads next; the raw result goes to the run dir.
    Tools have their own timeout; the request caps only limit new model calls."""
    ref = f"tool_{len(state.work.tool_results)}"
    res = tools.run(name, args, ctx.workdir)
    (ctx.rundir / f"{ref}.json").write_text(json.dumps({"call": {"name": name, "args": args}, "result": res},
                                                       ensure_ascii=False, indent=1), encoding="utf-8")
    state.work.tool_results[ref] = res
    out = (res.get("stdout") or res.get("content") or "").strip()
    ctx.trace.write("tool", ref=ref, call={"name": name, "args": args}, exit=res.get("exit"), out=out[:500],
                    stderr=(res.get("stderr") or "")[-300:])
    if res.get("exit") != 0:
        out += f"\n(exit {res.get('exit')}) {(res.get('stderr') or '').strip()}"
    if res.get("image") and ctx.is_model("perception"):
        seen = len(state.work.observations)
        perception.look(ctx, state, images=[res["image"]])
        out += "".join(f"\n{o.summary}" for o in state.work.observations[seen:])
    return out.strip() or "(no output)"


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
        name, args = (call.get("function") or {}).get("name", ""), tools.arguments(call)
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


def act(ctx, state, request):
    specs = tools.specs(HAND)
    msgs = [{"role": "system", "content": SYS}, {"role": "user", "content": request}]
    # one round of tools, then the answer. the second call gets no tools, so if more is needed it goes back through
    # reasoning, which holds the plan
    r = ctx.chat(state, "motor", msgs, tools=specs)
    if not r.tool_calls:
        return r.text.strip() or "The tool hand made no tool calls."
    ran = call_tools(ctx, state, r, msgs, specs)
    said = ctx.chat(state, "motor", msgs).text.strip()
    if said:
        return said
    # it ran the tools and said nothing about them; the raw results are all there is to hand over
    return "\n\n".join(f"{name} {json.dumps(args, ensure_ascii=False)[:300]}:\n{out}" for name, args, out in ran) \
        or "The tool hand made no tool calls."
