"""Executive: the model classifies the goal, nothing else is decided here. It writes no plan: a plan in the
shared view made the 1.2B's reading of the task everyone's premise (PREREG-v3)."""
from ..schema import Confidence, Envelope, Next

CLASS_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["kind", "needs_tool"],
                "properties": {"kind": {"enum": ["chat", "math", "code", "qa"]}, "needs_tool": {"type": "boolean"}}}
CLASS_SYS = """Classify the user's message into one kind:
- chat: a greeting, thanks, or small talk with nothing to look up or work out.
- code: the user wants source code written, completed, fixed or explained (a function, a script, a class, a regex).
- math: the answer is a number or quantity to work out from the message: arithmetic, a word problem, dates, counting, unit conversion.
- qa: a fact or explanation to answer from knowledge; nothing to compute.
needs_tool: true when running a program would help get the answer right (any calculation, hashing, file or web access); false for chat, trivia and opinions.
Examples:
"hey, how's it going" -> chat, false
"Complete this python function: def is_palindrome(s: str):" -> code, true
"how do I reverse a string in javascript" -> code, false
"A train leaves at 3pm going 60 mph. How far has it gone by 5:30pm?" -> math, true
"what is 2 to the power 100 modulo 97" -> math, true
"who painted the Mona Lisa" -> qa, false
"read notes.txt and tell me how many lines mention Tuesday" -> qa, true"""
FAST_SYS = "You are Lobes, a local assistant. Reply in one or two sentences, in the user's language."


def intake(ctx, state):
    if state.images:
        state.task_class = "vision"
        return
    if not ctx.is_model("executive"):
        state.needs_tool = True        # no classifier: the program witnesses run for everything
        return
    r = ctx.chat(state, "executive", [{"role": "system", "content": CLASS_SYS}, {"role": "user", "content": state.goal.strip()}],
                 schema=CLASS_SCHEMA, thinking=False, max_tokens=40)
    kind = state.kind = r.data["kind"] if r.data else "qa"
    if kind == "chat":
        state.task_class, state.route = "chat", "fast"
    else:
        # code is not a route: the 1B cannot tell "write a program" from "use one", so a program witness runs
        # and the reasoning lobe hands back source only when the goal wants source
        state.needs_tool = kind in ("math", "code") or not r.data or r.data["needs_tool"]


def fast(ctx, state):
    lobe = "executive" if ctx.is_model("executive") else "reasoning"
    r = ctx.chat(state, lobe, [{"role": "system", "content": FAST_SYS}, {"role": "user", "content": state.goal}],
                 thinking=False, max_tokens=200)
    return Envelope(kind="final", goal=state.goal, answer=r.text.strip(),
                    confidence=Confidence(score=0.5, basis="self"), next=Next(action="answer"))
