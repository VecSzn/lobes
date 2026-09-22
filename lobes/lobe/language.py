"""Language: the review model. Run after the draft, it reads the request, every tool call with its real result and
the draft, then passes the draft or says what's wrong with it. Run before the draft, it reads only the request and
lists what the reply has to cover, for the expert's brief."""
import json

from .reasoning import brief

SYS = """You check an assistant's work before the user sees it. You get the user's request, every program or tool the
assistant ran with its real result, and the assistant's draft reply.

If the draft answers the request correctly and agrees with the results, reply with only OK.

If something is wrong, say exactly what is wrong and what to change: a program does not compute what the request asks,
a result contradicts the draft, part of the request is unanswered, or the draft has another mistake you can point to."""

ASKS = """You read a user's request for the assistant who is about to answer it, and write down what their reply has
to satisfy.

Write one short line per thing the request asks of the reply. Take them from the request itself and keep its own
words and numbers; do not add any of your own, and do not answer the request."""
# forced to a list: told only in words not to answer, a small model answered the request anyway, and that answer
# would end up in the expert's brief
LIST = {"type": "object", "additionalProperties": False, "required": ["requirements"],
        "properties": {"requirements": {"type": "array", "minItems": 1, "maxItems": 16,
                                        "items": {"type": "string", "maxLength": 300}}}}


def work(state):
    parts = [f"## Request\n{brief(state)}"]
    for name, args, out in state.work.ran:
        body = args.get("code") if name == "python" else args.get("request") if name == "motor" else json.dumps(args, ensure_ascii=False)
        parts.append(f"## The assistant ran {name}\n```\n{body}\n```\nResult:\n```\n{out[-3000:]}\n```")
    parts.append(f"## Draft reply\n{state.work.draft}")
    return "\n\n".join(parts)


def requirements(ctx, state):
    """-> what the request asks the reply to satisfy, in the reviewer's words, for the expert's brief.
    Wrong answers mostly missed one condition out of several, and reviewing the draft rarely caught which. A small
    model can copy the conditions out but is bad at checking a draft against them, so here it lists them first."""
    r = ctx.chat(state, "language", [{"role": "system", "content": ASKS},
                                     {"role": "user", "content": brief(state)}],
                 schema=LIST, temperature=0, max_tokens=512)
    if not r.data:            # the grammar makes that unlikely; a request with no reading of it goes as written
        return ""
    return "\n".join(f"- {line.strip()}" for line in r.data["requirements"] if line.strip())


def review(ctx, state, lobe="language"):
    """-> (True, what it wrote) or (False, the problem). The verdict is plain text, OK on the first line. As a tool
    call it depended on the model writing the call marker, and a rejection written without one passed as text.
    lobe "check" has the expert read its own draft."""
    text = ctx.chat(state, lobe, [{"role": "system", "content": SYS}, {"role": "user", "content": work(state)}]).text.strip()
    return not text or text.splitlines()[0].strip(" .!*`").upper() == "OK", text
