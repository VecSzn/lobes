"""Executive: the model classifies the goal (no rules, same prompt as v3) and writes the plan."""
from ..schema import Confidence, Envelope, Next
from . import brief

CLASS_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["kind", "needs_tool"],
                "properties": {"kind": {"enum": ["chat", "math", "code", "qa"]}, "needs_tool": {"type": "boolean"}}}
PLAN_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["steps", "first"],
               "properties": {"steps": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 4},
                              "first": {"enum": ["tool", "reason"]}}}

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
PLAN_SYS = ("You are the executive lobe of a small assistant. Write 1-4 short steps to reach the goal, then say "
            "whether to start with a tool (python) or with reasoning.")
FAST_SYS = "You are Lobes, a local assistant. Reply in one or two sentences, in the user's language."


def intake(ctx, state):
    if state.images:
        state.task_class = "vision"
        return
    if not ctx.is_model("executive"):
        state.needs_tool = True        # no classifier: the tool path runs for everything
        return
    r = ctx.chat(state, "executive", [{"role": "system", "content": CLASS_SYS}, {"role": "user", "content": state.goal.strip()}],
                 schema=CLASS_SCHEMA, thinking=False, max_tokens=40)
    kind = r.data["kind"] if r.data else "qa"
    if kind == "chat":
        state.task_class, state.route = "chat", "fast"
    elif kind == "code":
        state.task_class = "code"
    elif kind == "math":
        state.task_class, state.needs_tool = "math", True
    else:
        state.needs_tool = not r.data or r.data["needs_tool"]


def plan(ctx, state):
    steps, first = ["solve", "verify"], "tool" if state.needs_tool else "reason"
    if ctx.is_model("executive"):
        r = ctx.chat(state, "executive", [{"role": "system", "content": PLAN_SYS}, {"role": "user", "content": brief(state)}],
                     schema=PLAN_SCHEMA, thinking=False, max_tokens=200)
        if r.data:
            steps = r.data["steps"]
            if not state.needs_tool:            # the classifier's needs_tool wins over the plan's first step
                first = r.data["first"]
    return Envelope(kind="plan", goal=state.goal, answer="; ".join(steps),
                    next=Next(action="tool" if first == "tool" else "answer",
                              module="motor" if first == "tool" else "reasoning"))


def fast(ctx, state):
    lobe = "executive" if ctx.is_model("executive") else "reasoning"
    r = ctx.chat(state, lobe, [{"role": "system", "content": FAST_SYS}, {"role": "user", "content": state.goal}],
                 thinking=False, max_tokens=200)
    return Envelope(kind="final", goal=state.goal, answer=r.text.strip(),
                    confidence=Confidence(score=0.5, basis="self"), next=Next(action="answer"))
