"""One chat() for every OpenAI-compatible endpoint: llama-server, LM Studio, OpenAI, DeepSeek, ...

Structured output goes through response_format json_schema. llama-server turns that into a
grammar, so locally the JSON is valid by construction; remote providers mostly honour it too.
"""
import base64
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


def _image_part(path):
    p = Path(path)
    mime = "image/png" if p.suffix.lower() == ".png" else "image/jpeg"
    b64 = base64.b64encode(p.read_bytes()).decode()
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}


def chat(provider, model, messages, *, schema=None, images=None, thinking=None,
         temperature=0.2, max_tokens=2048, seed=None, timeout=600.0):
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
    r = httpx.post(provider["base_url"].rstrip("/") + "/chat/completions", json=body, headers=headers, timeout=timeout)
    r.raise_for_status()
    j = r.json()
    msg = j["choices"][0]["message"]
    text = msg.get("content") or ""
    data = None
    if schema is not None:
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = None
    return Reply(
        text=text,
        data=data,
        reasoning=msg.get("reasoning_content") or msg.get("reasoning"),
        usage=j.get("usage", {}),
        ms=int((time.perf_counter() - t0) * 1000),
        timings=j.get("timings", {}),
    )


def list_models(provider, timeout=10.0):
    headers = {"Authorization": f"Bearer {provider['api_key']}"} if provider.get("api_key") else {}
    r = httpx.get(provider["base_url"].rstrip("/") + "/models", headers=headers, timeout=timeout)
    r.raise_for_status()
    return [m["id"] for m in r.json().get("data", [])]
