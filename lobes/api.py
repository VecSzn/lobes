"""/v1/chat/completions in front of the runner, so anything that talks to OpenAI can talk to the lobes.
model "lobes/<profile>" picks a profile, "lobes-v1" is the v1 profile. If a request carries tools, calls to them come
back for the client to run, like with any chat model; without tools the lobes use their own.
stream=true sends the relay's work as reasoning_content and the answer as content."""
import asyncio
import base64
import json
import threading
import time
import uuid

import httpx
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse, StreamingResponse

from .runner import request_at, run

MODELS = {"lobes-v1": "v1"}
CONTEXT = "Current runtime context"     # DeepSeek Harness sends its workspace and policy snapshot as a user message


def _text(content, images=None, imgdir=None):
    """Text parts joined. Data-URL images are written to disk when images is a list, because providers.chat takes paths."""
    if not isinstance(content, list):
        return content or ""
    parts = []
    for p in content:
        if p.get("type") == "text":
            parts.append(p["text"])
        elif p.get("type") == "image_url":
            url = p["image_url"]["url"]
            if images is None or not url.startswith("data:"):
                parts.append("[image]")
                continue
            head, b64 = url.split(",", 1)
            f = imgdir / f"{uuid.uuid4().hex[:8]}.{'png' if 'png' in head else 'jpg'}"
            f.write_bytes(base64.b64decode(b64))
            images.append(str(f))
    return "\n".join(parts)


def _messages(messages, imgdir):
    """-> (messages for the models, goal, image paths). Images are read only from a turn's request before any tool
    step; a client's tool step must not send them through perception again. Reasoning a client echoes back is dropped:
    it holds the relay's notes, and a turn rendered the same way every step keeps the server's prompt cache.
    A harness's runtime-context snapshot is not a request: the latest one joins the system prompt."""
    snapshots = [m for m in messages if m["role"] == "user" and _text(m.get("content")).startswith(CONTEXT)]
    messages = [m for m in messages if m not in snapshots]
    images, out, at = [], [], request_at(messages)
    for i, m in enumerate(messages):
        fresh = i == at and not any(n["role"] == "assistant" for n in messages[i + 1:])
        msg = {"role": "system" if m["role"] == "developer" else m["role"],
               "content": _text(m.get("content"), images if fresh else None, imgdir)}
        msg.update({k: m[k] for k in ("tool_calls", "tool_call_id") if m.get(k)})
        out.append(msg)
    if snapshots:
        note = _text(snapshots[-1].get("content"))
        if out and out[0]["role"] == "system":
            out[0]["content"] += "\n\n" + note
        else:
            out.insert(0, {"role": "system", "content": note})
    at = request_at(out)
    goal = None if at is None else out[at]["content"]
    return out, goal, images


def usage(state):
    """prompt_tokens is the conversation as the expert last read it: a client sizes its context from it, and the
    router, classifier and review calls are not in that conversation. Every call's tokens summed go under lobes."""
    last = state.spend.context or state.spend.usage
    out = {"prompt_tokens": last.get("prompt_tokens", 0), "completion_tokens": state.spend.usage.get("completion_tokens", 0)}
    out["total_tokens"] = out["prompt_tokens"] + out["completion_tokens"]
    if last.get("prompt_tokens_details"):
        out["prompt_tokens_details"] = last["prompt_tokens_details"]
    return out


def failure(exc):
    """-> (status, openai error) for an exception out of run. A server's error keeps its status and message, which is
    how a client tells a request over the context size, one it can compact, from a crash."""
    status, message = 500, str(exc)
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        try:
            message = exc.response.json()["error"]["message"]
        except Exception:       # noqa: BLE001 - not llama-server's error shape
            message = exc.response.text or message
    # llama-server's "Context size has been exceeded." is slots sharing the context at once, gone on a retry
    code = "context_length_exceeded" if "exceeds the available context size" in message else None
    return status, {"message": message[:1000], "type": "invalid_request_error" if status < 500 else "server_error", "code": code}


def make_route(cfg, default_profile=None):
    imgdir = cfg["_root"] / "runs" / "_api_images"
    imgdir.mkdir(parents=True, exist_ok=True)

    async def completions(request):
        body = await request.json()
        model = body.get("model") or ""
        profile = MODELS.get(model) or (model[6:] if model.startswith("lobes/") else default_profile or cfg["profile"])
        if profile not in cfg["profiles"]:
            return JSONResponse({"error": {"message": f"unknown model {model}"}}, status_code=404)
        messages, goal, images = _messages(body.get("messages", []), imgdir)
        if goal is None:
            return JSONResponse({"error": {"message": "messages need a user message"}}, status_code=400)
        c = dict(cfg, effort=body["reasoning_effort"]) if body.get("reasoning_effort") else cfg   # openai's field name
        kw = dict(profile=profile, images=images, messages=messages, client_tools=body.get("tools") if "tools" in body else None)
        rid, created, name = f"chatcmpl-{uuid.uuid4().hex[:12]}", int(time.time()), model or f"lobes/{profile}"

        def done(state):
            extra = {"task_id": state.task_id, "route": state.turn.route, "topic": state.turn.topic,
                     "sent_back": state.turn.problems, "swaps": state.spend.swaps, "ms": state.spend.ms(),
                     "uncertainties": state.uncertainties, "usage": state.spend.usage}
            return usage(state), extra, "tool_calls" if state.tool_calls else "stop"

        if not body.get("stream"):
            try:
                state = await run_in_threadpool(run, c, goal, **kw)
            except Exception as e:      # noqa: BLE001
                status, error = failure(e)
                return JSONResponse({"error": error}, status_code=status)
            tokens, extra, finish = done(state)
            message = {"role": "assistant", "content": state.answer}
            if state.tool_calls:
                message["tool_calls"] = state.tool_calls
            return JSONResponse({"id": rid, "object": "chat.completion", "created": created, "model": name,
                                 "choices": [{"index": 0, "message": message, "finish_reason": finish}],
                                 "usage": tokens, "lobes": extra})

        def chunk(delta=None, finish=None, **more):
            choices = [] if delta is None and finish is None else [{"index": 0, "delta": delta or {}, "finish_reason": finish}]
            return "data: " + json.dumps({"id": rid, "object": "chat.completion.chunk", "created": created, "model": name,
                                          "choices": choices, **more}, ensure_ascii=False) + "\n\n"

        loop, queue, cancel = asyncio.get_running_loop(), asyncio.Queue(), threading.Event()

        def on_delta(kind, text):
            loop.call_soon_threadsafe(queue.put_nowait, (kind, text))

        async def work():
            try:
                return await run_in_threadpool(run, c, goal, on_delta=on_delta, cancel=cancel, **kw)
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)     # behind the deltas the thread already queued

        async def sse():
            task = asyncio.ensure_future(work())
            try:
                yield chunk({"role": "assistant", "content": ""})
                shown = gap = False
                while (item := await queue.get()) is not None:
                    kind, text = item
                    if kind == "content" and gap:       # a later reply's text, after a tool step or a cut retry
                        yield chunk({"content": "\n\n"})
                    shown, gap = shown or kind == "content", shown and kind != "content"
                    yield chunk({"content" if kind == "content" else "reasoning_content": text})
                try:
                    state = await task
                except Exception as e:  # noqa: BLE001 - the 200 is already out, so the error goes in the stream
                    yield "data: " + json.dumps({"error": failure(e)[1]}, ensure_ascii=False) + "\n\n"
                    return
                tokens, extra, finish = done(state)
                if state.answer and not shown:      # a hard draft waited for its review
                    yield chunk({"content": state.answer})
                elif state.stopped:                 # why the relay stopped, after the text already sent
                    yield chunk({"content": "\n\n" + state.answer})
                if state.tool_calls:
                    yield chunk({"tool_calls": [dict(call, index=i) for i, call in enumerate(state.tool_calls)]})
                yield chunk(finish=finish)
                yield chunk(usage=tokens, lobes=extra)
                yield "data: [DONE]\n\n"
            finally:
                cancel.set()        # uvicorn cancels this generator when the client disconnects
        return StreamingResponse(sse(), media_type="text/event-stream")

    return completions
