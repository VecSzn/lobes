"""One chat() for every OpenAI-compatible endpoint: llama-server, LM Studio, OpenAI, DeepSeek, ...

Structured output goes through response_format json_schema. llama-server turns that into a
grammar, so locally the JSON is valid by construction; remote providers mostly honour it too.
"""
import base64
import io
import json
import time
from dataclasses import dataclass
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
    forced: bool = False          # thinking hit the cap and the answer was forced out of the partial reasoning


MIN_SIDE = 768   # OCRBench crops are often 200 px tall; the vision encoder reads them better blown up
BUDGET_MSG = "Considering the limited time by the user, I have to give the solution based on the thinking directly now."


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


def chat(provider, model, messages, *, schema=None, images=None, thinking=None,
         temperature=0.2, max_tokens=2048, seed=None, timeout=600.0, ctx=None):
    messages = [dict(m) for m in messages]
    if images:
        last = messages[-1]
        last["content"] = [{"type": "text", "text": last["content"]}] + [_image_part(p) for p in images]

    body = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
    if seed is not None:
        body["seed"] = seed
    if schema is not None and provider.get("json") == "object":
        # providers without json_schema support (deepseek): json mode plus the schema pasted into the prompt
        body["response_format"] = {"type": "json_object"}
        messages.insert(0, {"role": "system", "content": "Reply with JSON matching this schema:\n" + json.dumps(schema)})
    elif schema is not None:
        body["response_format"] = {"type": "json_schema", "json_schema": {"name": "out", "schema": schema}}
    if thinking is not None:
        # Qwen3.5 and Nemotron 3 both read enable_thinking from the chat template; llama-server passes it through
        body["chat_template_kwargs"] = {"enable_thinking": bool(thinking)}

    headers = {}
    if provider.get("api_key"):
        headers["Authorization"] = f"Bearer {provider['api_key']}"

    t0 = time.perf_counter()
    j = _post(provider, body, headers, timeout)
    msg = j["choices"][0]["message"]
    reasoning = msg.get("reasoning_content") or msg.get("reasoning")
    usage = j.get("usage", {})
    forced = False
    if j["choices"][0].get("finish_reason") == "length" and reasoning and not msg.get("content") and not provider.get("api_key"):
        # thinking ate the whole cap. llama-server prefills a trailing assistant turn, and the chat template only
        # closes the think block when content is non-empty, so the model answers from what it thought so far.
        if ctx:
            # the re-send carries the reasoning as prompt; cut it so prompt + reasoning + answer fit the context
            room = ctx - usage.get("prompt_tokens", 0) - 2600
            got = usage.get("completion_tokens") or 1
            if room < got:
                reasoning = reasoning[: max(0, len(reasoning) * room // got)]
        body["messages"] = messages + [{"role": "assistant", "reasoning_content": reasoning + "\n\n" + BUDGET_MSG,
                                        "content": "{" if schema is not None else " "}]
        body["max_tokens"] = 2500
        try:
            j = _post(provider, body, headers, timeout)
        except httpx.HTTPStatusError:
            # gemma4's template folds the prefilled reasoning into the grammar and llama-server then rejects it (400)
            body.pop("response_format", None)
            j = _post(provider, body, headers, timeout)
        msg = j["choices"][0]["message"]
        usage = {k: usage.get(k, 0) + j.get("usage", {}).get(k, 0) for k in ("prompt_tokens", "completion_tokens", "total_tokens")}
        forced = True
    text = msg.get("content") or ""
    if forced and schema is not None and not text.lstrip().startswith("{"):
        text = "{" + text          # the prefilled brace comes back with the content on some builds, not on others
    data = None
    if schema is not None:
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = None
        if not isinstance(data, dict) or any(k not in data for k in schema.get("required", ())):
            data = None       # the no-grammar fallback above can come back with the wrong keys
    return Reply(
        text=text,
        data=data,
        reasoning=reasoning,
        usage=usage,
        ms=int((time.perf_counter() - t0) * 1000),
        timings=j.get("timings", {}),
        finish=j["choices"][0].get("finish_reason"),
        forced=forced,
    )


def _post(provider, body, headers, timeout):
    r = httpx.post(provider["base_url"].rstrip("/") + "/chat/completions", json=body, headers=headers, timeout=timeout)
    r.raise_for_status()
    return r.json()


def list_models(provider, timeout=10.0):
    headers = {"Authorization": f"Bearer {provider['api_key']}"} if provider.get("api_key") else {}
    r = httpx.get(provider["base_url"].rstrip("/") + "/models", headers=headers, timeout=timeout)
    r.raise_for_status()
    return [m["id"] for m in r.json().get("data", [])]
