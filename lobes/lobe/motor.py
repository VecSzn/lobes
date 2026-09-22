"""Motor: the tool hand. Reasoning says in words what it needs from files, the shell, the web or the screen; motor
makes the tool calls, reads the results and answers from them.
The raw results stay here. Passed through, a `cat` of a file would sit in the solver's conversation and get
prefilled again on every later turn."""
import json

from .. import tools

SYS = ("You are the tool hand. Carry out the request with tool calls; relative paths are in the work dir. Then "
       "answer the request from what they returned. Whoever asked cannot see the results, so your reply has to "
       "carry what they need out of them, including anything that failed or came back empty.")


def act(ctx, state, request):
    from ..runner import call_tools
    specs = tools.specs()
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
