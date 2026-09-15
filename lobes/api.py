"""lobes api: /v1/chat/completions in front of the runner, so anything that talks to OpenAI can talk to the
lobes. The last user message is the goal, earlier turns are pasted in as context. model = "lobes/<profile>"."""
import base64
import json
import time
import uuid

import uvicorn
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from .runner import run


def _goal(messages, imgdir):
    """-> (goal text, image paths). Data-URL images are written to disk because providers.chat takes paths."""
    images, turns = [], []
    for m in messages:
        c = m.get("content") or ""
        if isinstance(c, list):
            parts = []
            for p in c:
                if p.get("type") == "text":
                    parts.append(p["text"])
                elif p.get("type") == "image_url" and p["image_url"]["url"].startswith("data:"):
                    head, b64 = p["image_url"]["url"].split(",", 1)
                    f = imgdir / f"{uuid.uuid4().hex[:8]}.{'png' if 'png' in head else 'jpg'}"
                    f.write_bytes(base64.b64decode(b64))
                    images.append(str(f))
            c = "\n".join(parts)
        turns.append((m["role"], c))
    goal = next((c for r, c in reversed(turns) if r == "user"), "")
    earlier = [f"{r}: {c}" for r, c in turns[:-1] if c and r != "system"]
    if earlier:
        goal = "Earlier in this conversation:\n" + "\n".join(earlier)[-4000:] + f"\n\nNow: {goal}"
    return goal, images


def make_app(cfg, default_profile=None):
    imgdir = cfg["_root"] / "runs" / "_api_images"
    imgdir.mkdir(parents=True, exist_ok=True)

    async def completions(request):
        body = await request.json()
        model = body.get("model") or ""
        profile = model.split("/", 1)[1] if model.startswith("lobes/") else default_profile or cfg["profile"]
        goal, images = _goal(body.get("messages", []), imgdir)
        c = dict(cfg, effort=body["reasoning_effort"]) if body.get("reasoning_effort") else cfg   # openai's field name
        state = await run_in_threadpool(run, c, goal, profile=profile, images=images)
        usage = {k: state.usage.get(k, 0) for k in ("prompt_tokens", "completion_tokens", "total_tokens")}
        extra = {"task_id": state.task_id, "task_class": state.task_class, "steps": state.steps, "retries": state.retries,
                 "swaps": state.swaps, "ms": state.ms(),
                 "verdicts": [v.verdict for v in state.verdicts]}
        rid, created = f"chatcmpl-{state.task_id}", int(time.time())
        if body.get("stream"):
            chunk = {"id": rid, "object": "chat.completion.chunk", "created": created, "model": f"lobes/{profile}",
                     "choices": [{"index": 0, "delta": {"role": "assistant", "content": state.answer}, "finish_reason": "stop"}],
                     "usage": usage, "lobes": extra}

            async def sse():
                yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\ndata: [DONE]\n\n"
            return StreamingResponse(sse(), media_type="text/event-stream")
        return JSONResponse({"id": rid, "object": "chat.completion", "created": created, "model": f"lobes/{profile}",
                             "choices": [{"index": 0, "message": {"role": "assistant", "content": state.answer},
                                          "finish_reason": "stop"}],
                             "usage": usage, "lobes": extra})

    async def models(request):
        return JSONResponse({"object": "list", "data": [{"id": f"lobes/{p}", "object": "model", "owned_by": "lobes"}
                                                        for p in cfg["profiles"]]})

    return Starlette(routes=[Route("/v1/chat/completions", completions, methods=["POST"]),
                             Route("/v1/models", models)])


def serve(cfg, host, port, profile=None):
    uvicorn.run(make_app(cfg, profile), host=host, port=port, log_level="warning")
