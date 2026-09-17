"""One chat() for the OpenAI-compatible endpoints on this machine: llama-server, LM Studio.

Structured output goes through response_format json_schema. llama-server turns that into a
grammar, so the JSON is valid by construction.
"""
import base64
import io
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx


@dataclass
class Reply:
    text: str
    data: dict | list | None      # parsed JSON when a schema was given
    reasoning: str | None         # <think> content if the server split it out
    usage: dict
    ms: int
    timings: dict                 # llama-server only: prompt_n, predicted_n, predicted_per_second ...
    finish: str | None = None     # "length" means the answer was cut off by max_tokens
    tool_calls: list = field(default_factory=list)   # openai format, ready to send back in the assistant turn


MIN_SIDE = 768   # Preserve legibility when sending small images to the vision encoder.
# llama-server writes it into the thinking where the budget ends it. "Time to answer." on its own left the
# model free to derive everything again in the body: on GPQA that second pass filled the answer room and got
# cut before it named an answer, on 48% of items. It reaches every lobe, review included, so it asks for the
# conclusion each one owes rather than for an answer.
BUDGET_MESSAGE = ("\nOut of thinking time. Give your conclusion now, from what you worked out above. "
                  "Do not start over.\n")
# gemma-4-E2B thought 0.3-2.8K tokens with the switch off on 39 reviews in 750 items, saving none; a budget of 0
# moved the thought into the answer, and an empty thought block in the prompt made most reviews an essay
UNASKED_THOUGHT = 256


def _image_part(path):
    p = Path(path)
    mime = "image/png" if p.suffix.lower() == ".png" else "image/jpeg"
    raw = p.read_bytes()
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(raw))
        if min(im.size) < MIN_SIDE:
            k = MIN_SIDE / min(im.size)
            im = im.convert("RGB").resize((round(im.width * k), round(im.height * k)), Image.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, "PNG")
            raw, mime = buf.getvalue(), "image/png"
    except Exception:
        pass            # not an image pillow can open; send it as-is and let the model complain
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{base64.b64encode(raw).decode()}"}}


def chat(provider, model, messages, *, schema=None, images=None, thinking=None, thinking_budget=None, tools=None,
         temperature=0.2, max_tokens=2048, seed=None, timeout=600.0, ctx=None, on_delta=None):
    """on_delta(kind, text) streams the reply: kind is "reasoning", "content" or "tool_call" (argument text).
    The Reply is the same either way."""
    messages = [dict(m) for m in messages]
    for m in messages:      # a call cut off by max_tokens kept half its json; the chat template refuses it with a 500
        if m.get("tool_calls"):
            m["tool_calls"] = [c if _parses((c.get("function") or {}).get("arguments"))
                               else {**c, "function": {**c.get("function", {}), "arguments": "{}"}} for c in m["tool_calls"]]
    if images:
        last = messages[-1]
        last["content"] = [{"type": "text", "text": last["content"]}] + [_image_part(p) for p in images]

    body = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
    if seed is not None:
        body["seed"] = seed
    if schema is not None:
        # the grammar only constrains tokens: a model that was not told the format plans prose and the grammar
        # then mangles it (gemma answered "}54"), so the schema is in the prompt too
        hint = "Reply with JSON matching this schema:\n" + json.dumps(schema)
        if messages[0]["role"] == "system":
            messages[0]["content"] += "\n" + hint
        else:
            messages.insert(0, {"role": "system", "content": hint})
        body["response_format"] = {"type": "json_schema", "json_schema": {"name": "out", "schema": schema}}
    # Explicit for templates that support this switch; other templates may ignore it.
    body["chat_template_kwargs"] = {"enable_thinking": bool(thinking)}
    if not thinking or thinking_budget is not None:    # llama-server ends the thinking there and lets the model answer
        body["thinking_budget_tokens"] = thinking_budget if thinking else UNASKED_THOUGHT
        body["reasoning_budget_message"] = BUDGET_MESSAGE
    if tools:
        body["tools"] = tools

    t0 = time.perf_counter()
    j = _stream(provider, body, timeout, on_delta) if on_delta else _post(provider, body, timeout)
    msg = j["choices"][0]["message"]
    reasoning = msg.get("reasoning_content") or msg.get("reasoning")
    usage = j.get("usage", {})
    text = msg.get("content") or ""
    data = None
    if schema is not None:
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = None
        if not isinstance(data, dict) or any(k not in data for k in schema.get("required", ())):
            data = None
    return Reply(
        text=text,
        data=data,
        reasoning=reasoning,
        usage=usage,
        ms=int((time.perf_counter() - t0) * 1000),
        timings=j.get("timings", {}),
        finish=j["choices"][0].get("finish_reason"),
        tool_calls=msg.get("tool_calls") or [],
    )


def _parses(arguments):
    if isinstance(arguments, dict):
        return True
    try:
        return isinstance(json.loads(arguments or "{}"), dict)
    except json.JSONDecodeError:
        return False


def _post(provider, body, timeout):
    r = httpx.post(provider["base_url"].rstrip("/") + "/chat/completions", json=body, timeout=timeout)
    r.raise_for_status()
    return r.json()


def _stream(provider, body, timeout, on_delta):
    """-> the json a plain post returns, assembled from the server-sent chunks. timeout bounds each read, not the whole reply."""
    body = dict(body, stream=True, stream_options={"include_usage": True})
    msg, calls, j = {"content": "", "reasoning_content": ""}, {}, {}
    with httpx.stream("POST", provider["base_url"].rstrip("/") + "/chat/completions", json=body, timeout=timeout) as r:
        if r.is_error:
            r.read()            # keeps the server's message on the error, e.g. that the request exceeds the context
        r.raise_for_status()
        for line in r.iter_lines():
            if not line.startswith("data: ") or line == "data: [DONE]":
                continue
            chunk = json.loads(line[6:])
            if chunk.get("error"):
                raise httpx.RemoteProtocolError(f"server error in stream: {chunk['error']}")
            j.update({k: chunk[k] for k in ("usage", "timings") if chunk.get(k)})
            for choice in chunk.get("choices") or []:
                delta = choice.get("delta") or {}
                for key, kind in (("reasoning_content", "reasoning"), ("content", "content")):
                    if delta.get(key):
                        msg[key] += delta[key]
                        on_delta(kind, delta[key])
                for part in delta.get("tool_calls") or []:
                    call = calls.setdefault(part.get("index", 0), {"id": "", "type": "function",
                                                                   "function": {"name": "", "arguments": ""}})
                    call["id"] = part.get("id") or call["id"]
                    for k in ("name", "arguments"):
                        call["function"][k] += (part.get("function") or {}).get(k) or ""
                    on_delta("tool_call", (part.get("function") or {}).get("arguments") or "")
                j["finish_reason"] = choice.get("finish_reason") or j.get("finish_reason")
    if not j.get("finish_reason"):      # the router unloaded the model mid-reply, say: a plain post gets a 500 there
        raise httpx.RemoteProtocolError("stream ended before the reply finished")
    msg["tool_calls"] = [calls[i] for i in sorted(calls)]
    return {"choices": [{"message": msg, "finish_reason": j.pop("finish_reason", None)}], **j}


def list_models(provider, timeout=10.0):
    r = httpx.get(provider["base_url"].rstrip("/") + "/models", timeout=timeout)
    r.raise_for_status()
    return [m["id"] for m in r.json().get("data", [])]
