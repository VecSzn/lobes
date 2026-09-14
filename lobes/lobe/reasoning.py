"""Reasoning: produces the candidate answer as claims + answer, or asks for one python run first."""
from .. import tools
from ..schema import Confidence, Envelope, Next, json_schema
from . import brief

SYS = """You are the reasoning lobe of a small local assistant. You get a goal and observations (tool outputs, image
descriptions) and reply with JSON matching the schema.
- answer: the final answer only, short. For code tasks: the complete code and nothing else, no fences.
- claims: the facts the answer rests on. support is "tool" only when the exact value appears in a tool observation,
  then evidence is that observation's ref (like tool_0). "derived" when you worked it out yourself. "assumed" when
  you are guessing.
- If running python would settle the question, set next.action to "tool" and put ONE python call in tool_calls
  that prints what you need. Otherwise next.action is "answer".
- uncertainties: what could be wrong, if anything."""


def solve(ctx, state):
    # a retry has to change something: first turn thinking on, then raise the temperature
    thinking = state.retries >= 1
    temperature = 0.7 if state.retries >= 2 else 0.2
    schema = json_schema(Envelope)
    schema["required"] = ["kind", "goal", "claims", "answer", "uncertainties", "next"]   # small models skip optional keys
    r = ctx.chat(state, "reasoning", [{"role": "system", "content": SYS}, {"role": "user", "content": brief(state)}],
                 schema=schema, thinking=thinking, temperature=temperature,
                 max_tokens=6000 if thinking else 2500)
    if r.data is None:
        return Envelope(kind="step_result", goal=state.goal, answer=r.text[:2000],
                        confidence=Confidence(score=0.2, basis="self"), next=Next(action="answer"),
                        uncertainties=["the model did not return valid json"])
    env = Envelope.model_validate(r.data)
    env.kind, env.goal = "step_result", state.goal
    env.tool_calls = [c for c in env.tool_calls if c.name in tools.TOOLS][:1]
    if env.next.action == "tool" and not env.tool_calls:
        env.next = Next(action="answer")
    return env
