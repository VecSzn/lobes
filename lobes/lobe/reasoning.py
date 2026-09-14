"""Reasoning: produces the candidate answer as claims + answer, or asks for one python run first."""
from .. import tools
from ..schema import Confidence, Envelope, Next, json_schema
from . import brief
from .verifier import backed_by, doctest_check, examples, same, unfence

schema = json_schema(Envelope)
schema["required"] = ["kind", "goal", "claims", "answer", "uncertainties", "next"]   # small models skip optional keys
REFLECT = {"type": "object", "additionalProperties": False, "required": ["flaw", "answer"],
           "properties": {"flaw": {"anyOf": [{"type": "string"}, {"type": "null"}]}, "answer": {"type": "string"}}}

SYS = """You are the reasoning lobe of a small local assistant. You get a goal and observations (tool outputs, image
descriptions) and reply with JSON matching the schema.
- answer: the final answer only, short. For code tasks: the complete code and nothing else, no fences.
- claims: the facts the answer rests on. support is "tool" only when the exact value appears in a tool observation,
  then evidence is that observation's ref (like tool_0). "derived" when you worked it out yourself. "assumed" when
  you are guessing.
- If running python would settle the question, or a tool observation you need is empty or wrong, set next.action
  to "tool" and put ONE python call in tool_calls that prints what you need. Otherwise next.action is "answer".
- uncertainties: what could be wrong, if anything."""

REFLECT_SYS = """You are the reasoning lobe, checking your own draft before it goes to the verifier. Read the goal,
the observations and the draft answer. Look for one concrete mistake: wrong arithmetic, a constraint in the goal the
draft ignores, a case the code does not handle, a claim no observation supports. Reply with JSON: flaw is one
sentence naming the mistake, or null if you find none; answer is the corrected answer, or the draft unchanged."""


def solve(ctx, state):
    # a retry has to change something: thinking on (medium turns it on here, high and up from the start),
    # then n hot samples and a vote. cfg["vote"] forces the vote from the start (the equal-compute single-model
    # control in the eval). closed-book qa always votes: with nothing to check against, the samples agreeing
    # is the only evidence. how many samples, and whether thinking is ever on, is the effort level.
    e = ctx.effort
    thinking = e["think"] == "always" or (e["think"] == "retry" and state.retries >= 1)
    n = ctx.cfg.get("vote") or e["n"]
    closed = state.task_class == "qa" and not state.tool_results and not state.images
    if state.task_class == "code" and examples(state.goal):
        return _best_code(ctx, state, thinking, n)
    if n == 1 or (state.retries < 2 and not ctx.cfg.get("vote") and not closed):
        return _one(ctx, state, thinking=thinking, temperature=0.2)
    envs = [_one(ctx, state, thinking=thinking, temperature=0.2 if i == 0 else 0.7) for i in range(n)]
    agree = [sum(same(e.answer, o.answer) for o in envs) for e in envs]
    best = envs[max(range(n), key=agree.__getitem__)]
    ctx.trace.write("vote", answers=[e.answer for e in envs], agree=max(agree))
    if max(agree) == n:
        best.confidence = Confidence(score=0.8, basis="consistency")
    elif max(agree) <= n // 2:
        best.uncertainties.append(f"{n} samples disagreed")
    return best


def _best_code(ctx, state, thinking, n):
    """The task carries its own >>> examples: run them on the first sample, and only if it fails draw n-1 more
    and keep the one that passes most. Costs nothing when the first is right."""
    env = _one(ctx, state, thinking=thinking, temperature=0.2)
    if env.next.action == "tool":
        return env
    score = [doctest_check(ctx, unfence(env.answer), state.goal)]
    envs = [env]
    if score[0][0] < score[0][1]:
        for _ in range(n - 1):
            envs.append(_one(ctx, state, thinking=thinking, temperature=0.7))
            score.append(doctest_check(ctx, unfence(envs[-1].answer), state.goal))
            if score[-1][0] == score[-1][1]:
                break
    i = max(range(len(envs)), key=lambda k: score[k][0])
    ctx.trace.write("best_of", passed=[s[0] for s in score], attempted=score[0][1], chosen=i)
    if score[i][0] == score[i][1] > 0:
        envs[i].confidence = Confidence(score=0.9, basis="evidence")   # passed its examples; reflect leaves it alone
    return envs[i]


def sample(ctx, state):
    """One more hot candidate for the runner's search; solve() gave the first."""
    return _one(ctx, state, thinking=ctx.effort["think"] != "never", temperature=0.7)


def reflect(ctx, state):
    """A second look at the draft by the same lobe. Evidence beats opinion: a draft that a tool printed, that
    passed its examples or that every sample agreed on is left alone. Otherwise a named flaw with a changed answer
    replaces the draft, and the change stays on record for the verifier and the final answer."""
    cand = state.candidate
    if not cand.answer or cand.confidence.basis in ("evidence", "consistency") or backed_by(state, unfence(cand.answer)):
        return
    thinking = ctx.effort["think"] != "never"
    msgs = [{"role": "system", "content": REFLECT_SYS}, {"role": "user", "content": brief(state, with_candidate=True)}]
    r = ctx.chat(state, "reasoning", msgs, schema=REFLECT, thinking=thinking, temperature=0.2,
                 max_tokens=_budget(ctx) if thinking else 2500)
    flaw, new = (r.data or {}).get("flaw"), (r.data or {}).get("answer") or ""
    changed = bool(flaw and new.strip() and not same(cand.answer, new))
    ctx.trace.write("reflect", flaw=flaw, changed=changed, answer=new[:500])
    if changed:
        cand.answer = new
        cand.confidence = Confidence(score=0.5, basis="self")
        cand.uncertainties.append(f"reflection changed the answer: {flaw}")


def _budget(ctx):
    return ctx.effort["budget"] or ctx.cfg.get("llama", {}).get("ctx", 16384)


def _one(ctx, state, *, thinking, temperature):
    msgs = [{"role": "system", "content": SYS}, {"role": "user", "content": brief(state)}]
    r = ctx.chat(state, "reasoning", msgs, schema=schema, thinking=thinking, temperature=temperature,
                 max_tokens=_budget(ctx) if thinking else 2500)
    if r.data is None and thinking:
        # the 9B thinks past the budget on some SimpleQA items and returns nothing; a plain answer beats none
        r = ctx.chat(state, "reasoning", msgs, schema=schema, thinking=False, temperature=temperature, max_tokens=2500)
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
