"""Executive: picks the route with rules, and if a model is configured, classifies the rest and writes the plan."""
import re

from ..schema import Confidence, Envelope, Next
from . import brief

GREET = re.compile(r"^\W*(hi|hello|hey|yo|thanks|thank you|good (morning|afternoon|evening|night)|how are you"
                   r"|what'?s up|你好|您好|嗨|哈喽|谢谢|早|早上好|晚安|在吗)\b", re.I)
CODE = re.compile(r"\b(function|def |class |implement|write .{0,20}(python|code|script|program)|regex|unit test"
                  r"|refactor)\b|函数|代码|实现|脚本", re.I)
MATH = re.compile(r"\d[\d,]*\s*[-+*/×÷^%]\s*\d|\b(sum|product|calculate|compute|how many|how much|solve|prime"
                  r"|factor|percent|average|total)\b|计算|多少|求", re.I)
TOOLY = re.compile(r"\b(run|execute|read|open|check|test|verify|benchmark|screenshot|screen|fetch|download|https?)\b"
                   r"|运行|执行|读取|文件|测试|截图|屏幕|网页", re.I)

CLASS_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["kind", "needs_tool"],
                "properties": {"kind": {"enum": ["chat", "math", "code", "qa"]}, "needs_tool": {"type": "boolean"}}}
PLAN_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["steps", "first"],
               "properties": {"steps": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 4},
                              "first": {"enum": ["tool", "reason"]}}}

CLASS_SYS = ("Classify the user's message. chat = greeting or small talk that needs no facts. math = numbers to "
             "compute. code = they want code written. qa = anything else. needs_tool = running python would help "
             "answer correctly.")
PLAN_SYS = ("You are the executive lobe of a small assistant. Write 1-4 short steps to reach the goal, then say "
            "whether to start with a tool (python) or with reasoning.")
FAST_SYS = "You are Lobes, a local assistant. Reply in one or two sentences, in the user's language."


def intake(ctx, state):
    g = state.goal.strip()
    if state.images:
        state.task_class = "vision"
    elif GREET.match(g) and len(g) < 40:
        state.task_class, state.route = "chat", "fast"
        return
    elif CODE.search(g):
        state.task_class = "code"
    elif MATH.search(g):
        state.task_class, state.needs_tool = "math", True
    elif ctx.is_model("executive"):
        r = ctx.chat(state, "executive", [{"role": "system", "content": CLASS_SYS}, {"role": "user", "content": g}],
                     schema=CLASS_SCHEMA, thinking=False, max_tokens=40)
        if r.data:
            state.task_class, state.needs_tool = r.data["kind"], r.data["needs_tool"]
            if state.task_class == "code" and TOOLY.search(g):
                state.task_class = "qa"     # lfm calls "fetch x"/"take a screenshot" code; that path runs the answer as python
            if state.task_class == "chat":
                state.route = "fast"
                return
    if TOOLY.search(g):
        state.needs_tool = True


def plan(ctx, state):
    steps, first = ["solve", "verify"], "tool" if state.needs_tool else "reason"
    if ctx.is_model("executive"):
        r = ctx.chat(state, "executive", [{"role": "system", "content": PLAN_SYS}, {"role": "user", "content": brief(state)}],
                     schema=PLAN_SCHEMA, thinking=False, max_tokens=200)
        if r.data:
            steps = r.data["steps"]
            if not state.needs_tool:            # rules win when they were sure
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
