"""Language: reads the request against the work, on whichever side of the draft the relay puts it. After it, it gets
the request, every tool reasoning ran with the real result, and the draft, and either passes the draft to the user as
written or sends the work back saying what is wrong. Before it, it gets the request alone and writes down what the
reply has to satisfy, for the expert's brief."""
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
# a list is all it can write: told in words not to answer the request, gemma-4-E2B answered it on 2 of the first 3
# items, and that answer would have reached the expert's brief as the reply it has to satisfy
LIST = {"type": "object", "additionalProperties": False, "required": ["requirements"],
        "properties": {"requirements": {"type": "array", "minItems": 1, "maxItems": 16,
                                        "items": {"type": "string", "maxLength": 300}}}}


def work(state):
    parts = [f"## Request\n{brief(state)}"]
    for name, args, out in state.ran:
        body = args.get("code") if name == "python" else args.get("request") if name == "motor" else json.dumps(args, ensure_ascii=False)
        parts.append(f"## The assistant ran {name}\n```\n{body}\n```\nResult:\n```\n{out[-3000:]}\n```")
    parts.append(f"## Draft reply\n{state.draft}")
    return "\n\n".join(parts)


def requirements(ctx, state):
    """-> what the request asks the reply to satisfy, in the reviewer's words, for the expert's brief.

    The other half of review's job, moved in front of the draft. Over 100 ifeval items every answer that scored
    wrong had met all of the request's conditions but one, and reading the draft afterwards found 2 of those 25:
    listing a condition is copying, checking one is counting, and a 2B does the first and not the second."""
    r = ctx.chat(state, "language", [{"role": "system", "content": ASKS},
                                     {"role": "user", "content": brief(state)}],
                 schema=LIST, temperature=0, max_tokens=512)
    if not r.data:            # the grammar makes that unlikely; a request with no reading of it goes as written
        return ""
    return "\n".join(f"- {line.strip()}" for line in r.data["requirements"] if line.strip())


def review(ctx, state, lobe="language"):
    """-> (True, what it wrote) or (False, the problem). A pass ships the draft: a rewrite cost a call's worth of tokens and
    once turned the reviewer's own remarks into the answer. The verdict is plain text because gemma-4-E2B wrote most
    of its send_back tool calls without the call marker, so they came back as text and passed. lobe "check" has the
    expert read its own draft."""
    text = ctx.chat(state, lobe, [{"role": "system", "content": SYS}, {"role": "user", "content": work(state)}]).text.strip()
    return not text or text.splitlines()[0].strip(" .!*`").upper() == "OK", text
