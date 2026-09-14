"""Motor: turns the goal into one concrete tool call. The grammar ties tool names to their argument shapes."""
from .. import tools
from ..schema import Envelope, Next, ToolCall
from . import brief

SYS = """You are the motor lobe. Pick ONE tool call that moves the goal forward.
python: a short script that prints the values needed, as plain numbers or strings, nothing decorative. Its output
becomes evidence, so print exactly what will be quoted. Do not repeat a call whose output is already listed."""


def act(ctx, state):
    schema = {"type": "object", "additionalProperties": False, "required": ["why", "call"],
              "properties": {"why": {"type": "string"}, "call": tools.call_schema()}}
    r = ctx.chat(state, "motor", [{"role": "system", "content": SYS}, {"role": "user", "content": brief(state)}],
                 schema=schema, thinking=False, max_tokens=1500)
    if r.data is None:
        return Envelope(kind="step_result", goal=state.goal, next=Next(action="answer"),
                        uncertainties=["motor returned no usable call"])
    return Envelope(kind="step_result", goal=state.goal, answer=r.data["why"],
                    tool_calls=[ToolCall(**r.data["call"])], next=Next(action="tool", module="motor"))
