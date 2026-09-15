"""Reasoning: a witness that answers from the goal and hands over a program that recomputes the answer. The same
function serves the verifier slot as a witness. Also the code path: implementations checked
against the task's own examples."""
from ..schema import Confidence, Envelope, Next, ToolCall
from . import Witness, brief
from .verifier import doctest_check, examples, restates, unfence

SYS = """You are a witness in a small local assistant. Solve the task from the goal and reply with JSON.
- answer: the final answer only, short: every value the goal asks for, nothing else.
- check: a python program that computes the answer from the goal's data on its own and prints exactly the values
  the goal asks for, the final one last. null when nothing about the task can be computed (trivia, opinions)."""
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["answer", "check"],
          "properties": {"answer": {"type": "string"}, "check": {"type": ["string", "null"]}}}
VISION_SYS = ("You are a witness in a small local assistant. You cannot see the image; the notes below are what the "
              "perception lobe and the ocr engine read from it. Reply with JSON: answer is the final answer only.")
ANSWER_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["answer"], "properties": {"answer": {"type": "string"}}}
CODE_SYS = ("You are the reasoning lobe of a small local assistant. Reply with JSON whose answer is the complete "
            "code the goal asks for, signature and imports included, no fences, no explanation.")


def _budget(ctx):
    return ctx.effort["budget"] or ctx.cfg.get("llama", {}).get("ctx", 16384)


def witness(ctx, state, lobe="reasoning", *, thinking=None, temperature=0.2):
    """One derivation by the model in `lobe`. Blind: it sees brief(state), never another witness. The check
    program's output is the value; the answer field stands in only when no program could run."""
    from ..runner import run_tool
    if thinking is None:
        thinking = ctx.effort["think"]
    vision = state.task_class == "vision"
    msgs = [{"role": "system", "content": VISION_SYS if vision else SYS}, {"role": "user", "content": brief(state)}]
    schema = ANSWER_SCHEMA if vision else SCHEMA
    r = ctx.chat(state, lobe, msgs, schema=schema, thinking=thinking, temperature=temperature,
                 max_tokens=_budget(ctx) if thinking else 2500)
    if r.data is None and thinking:
        # the model thinks past the budget on some items and returns nothing; a plain answer beats none
        r = ctx.chat(state, lobe, msgs, schema=schema, thinking=False, temperature=temperature, max_tokens=2500)
    if r.data is None:
        return Witness(lobe, r.text.strip()[:2000] or None, note="no json")
    answer, check = (r.data.get("answer") or "").strip(), r.data.get("check")
    if not check or restates(check, answer):
        return Witness(lobe, answer or None, note="restated" if check else "")
    for attempt in range(2):
        ref, res, out = run_tool(ctx, state, ToolCall(name="python", args={"code": check}))
        if out:
            return Witness(lobe, out, ran=True, ref=ref)
        if attempt == 0:
            state.retries += 1
            msgs += [{"role": "assistant", "content": r.text},
                     {"role": "user", "content": f"Your program printed nothing. exit={res.get('exit')} "
                                                 f"stderr: {(res.get('stderr') or '')[-800:]}\nFix the program."}]
            r2 = ctx.chat(state, lobe, msgs, schema=SCHEMA, thinking=False, temperature=temperature, max_tokens=2500)
            check = (r2.data or {}).get("check")
            if not check:
                break
    return Witness(lobe, answer or None, note="the program failed")


def code(ctx, state):
    """The task carries its own >>> examples: run them on the first sample, and only if it fails draw more
    (up to n) and keep the one that passes most. Costs nothing when the first is right."""
    thinking, n = ctx.effort["think"], ctx.effort["n"]
    envs, score = [], []
    for i in range(n if examples(state.goal) else 1):
        envs.append(_code_one(ctx, state, thinking, 0.2 if i == 0 else 0.7))
        score.append(doctest_check(ctx, unfence(envs[-1].answer), state.goal))
        if score[-1][0] == score[-1][1]:
            break
    i = max(range(len(envs)), key=lambda k: score[k][0])
    if len(envs) > 1:
        ctx.trace.write("best_of", passed=[s[0] for s in score], attempted=score[0][1], chosen=i)
    if score[i][0] == score[i][1] > 0:
        envs[i].confidence = Confidence(score=0.9, basis="evidence")   # passed its examples
    return envs[i]


def _code_one(ctx, state, thinking, temperature):
    msgs = [{"role": "system", "content": CODE_SYS}, {"role": "user", "content": brief(state)}]
    r = ctx.chat(state, "reasoning", msgs, schema=ANSWER_SCHEMA, thinking=thinking, temperature=temperature,
                 max_tokens=_budget(ctx) if thinking else 2500)
    if r.data is None and thinking:
        r = ctx.chat(state, "reasoning", msgs, schema=ANSWER_SCHEMA, thinking=False, temperature=temperature, max_tokens=2500)
    answer = (r.data or {}).get("answer") or r.text[:4000]
    return Envelope(kind="step_result", goal=state.goal, answer=answer, next=Next(action="answer"))
