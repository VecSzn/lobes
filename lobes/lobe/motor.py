"""Motor: the tool hand. Reasoning says in words what it needs from files, the shell, the web or the screen; motor
makes the tool calls, reads what came back and answers the request from it.

The results stay here. Handing them over raw made the relay's context the solver's context and nothing more: a
`cat` of a file put the whole file in the solver's conversation, where every later turn prefills it again. Two
models are only worth more than one if the second one holds what the first never has to."""
import json

from .. import tools

SYS = ("You are the tool hand. Carry out the request with tool calls; relative paths are in the work dir. Then "
       "answer the request from what they returned. Whoever asked cannot see the results, so your reply has to "
       "carry what they need out of them, including anything that failed or came back empty.")


def act(ctx, state, request):
    from ..runner import call_tools
    specs = tools.specs()
    msgs = [{"role": "system", "content": SYS}, {"role": "user", "content": request}]
    # One round of tools, then the answer. The second call has no tools, so a request that needs another step comes
    # back through reasoning, which is the lobe holding the plan. Before, that call could only say whether the hand
    # wanted more and its reply was thrown away: 123 of the hand's 315 calls over 59 repo items, 15.6% of the wall
    # time, for nothing.
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
