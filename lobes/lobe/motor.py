"""Motor: the first witness on anything computable. It reads the goal alone, picks one tool call, and what the
tool prints is its value. A failed call gets one repair with its own stderr."""
from .. import tools
from ..schema import ToolCall
from . import Witness, brief

SYS = """You are the motor lobe. Pick ONE tool call that produces the answer to the goal. Tools:
""" + tools.describe() + """
Prefer python for anything computable. print() exactly the values the goal asks for, plain, the final one last,
nothing decorative."""
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["why", "call"],
          "properties": {"why": {"type": "string"}, "call": tools.call_schema()}}


def witness(ctx, state):
    from ..runner import run_tool
    msgs = [{"role": "system", "content": SYS}, {"role": "user", "content": brief(state)}]
    for attempt in range(2):
        r = ctx.chat(state, "motor", msgs, schema=SCHEMA, thinking=False, max_tokens=1500)
        if r.data is None:
            return Witness("motor", None, note="no usable call")
        ref, res, out = run_tool(ctx, state, ToolCall(**r.data["call"]))
        if out:
            return Witness("motor", out, ran=True, ref=ref)
        if attempt == 0:
            state.retries += 1
            msgs += [{"role": "assistant", "content": r.text},
                     {"role": "user", "content": f"That call produced no output. exit={res.get('exit')} "
                                                 f"stderr: {(res.get('stderr') or '')[-800:]}\nFix it and call again."}]
    return Witness("motor", None, note="the call failed twice")
