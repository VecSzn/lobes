"""POST /v1/responses, the OpenAI Responses API that Codex speaks since it dropped chat completions. The input items
become the chat messages api.py takes, and the run goes out as Responses events: the models' thinking as quoted
commentary messages, the relay's steps as reasoning summaries, the answer as a final_answer message, calls to the
client's tools as function_call items.
Namespaced tools are flattened to namespace__name and split again on the way out. Hosted tools such as web_search
are dropped: the client expects the provider to run those."""
import asyncio
import json
import re
import threading
import time
import uuid

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse, StreamingResponse

from . import api, providers
from .runner import ALIASES, EFFORT


def _tools(tools):
    """-> (chat tools, flat name -> (namespace or None, name))"""
    out, names = [], {}

    def add(t, ns=None):
        flat = f"{ns}__{t['name']}" if ns else t["name"]
        names[flat] = (ns, t["name"])
        out.append({"type": "function", "function": {"name": flat, "description": t.get("description", ""),
                                                     "parameters": t.get("parameters") or {"type": "object"}}})
    for t in tools or []:
        if t.get("type") == "function":
            add(t)
        elif t.get("type") == "namespace":
            for inner in t.get("tools") or []:
                if inner.get("type") == "function":
                    add(inner, t["name"])
    return out, names


def _parts(content):
    """Responses content as chat content parts, which api._text joins."""
    if isinstance(content, str):
        return content
    parts = []
    for p in content or []:
        if p.get("type") in ("input_text", "output_text", "text"):
            parts.append({"type": "text", "text": p.get("text") or ""})
        elif p.get("type") == "input_image":
            parts.append({"type": "image_url", "image_url": {"url": p.get("image_url") or ""}})
    return parts


def _messages(body):
    """-> chat messages. Every instruction and developer message joins one leading system message: Qwen3.5's template
    rejects a system message anywhere else. Reasoning the client echoes back is dropped, as in api.py, and so is the
    thinking this file sent as quoted commentary; other commentary is the assistant's own text."""
    system, msgs = [body["instructions"]] if body.get("instructions") else [], []
    items = body.get("input") or []
    for it in [{"role": "user", "content": items}] if isinstance(items, str) else items:
        kind = it.get("type", "message")
        if kind == "message" and it.get("role") in ("system", "developer"):
            system.append(api._text(_parts(it.get("content"))))
        elif kind == "message" and not (it.get("phase") == "commentary" and api._text(_parts(it.get("content"))).startswith("> ")):
            msgs.append({"role": it["role"], "content": _parts(it.get("content"))})
        elif kind == "function_call":
            name = f"{it['namespace']}__{it['name']}" if it.get("namespace") else it["name"]
            call = {"id": it["call_id"], "type": "function", "function": {"name": name, "arguments": it.get("arguments") or "{}"}}
            if msgs and msgs[-1]["role"] == "assistant":      # one reply's text and calls are one chat turn
                msgs[-1].setdefault("tool_calls", []).append(call)
            else:
                msgs.append({"role": "assistant", "content": "", "tool_calls": [call]})
        elif kind == "function_call_output":
            out = it.get("output")
            msgs.append({"role": "tool", "tool_call_id": it["call_id"],
                         "content": _steady(out if isinstance(out, str) else api._text(_parts(out)))})
    return ([{"role": "system", "content": "\n\n".join(system)}] if system else []) + msgs


def _steady(text):
    """Codex heads a result with a random chunk id and the wall time, so the same command never gave the same result
    and runner's repeat checks let qwen3.5-4b run one failing disasm 13 times. Only the header before Output: goes."""
    head, sep, rest = text.partition("Output:")
    return re.sub(r"^(?:Chunk ID: \w+|Wall time:? [\d.]+ seconds)\r?\n", "", head, flags=re.M) + sep + rest if sep else text


def _running(items):
    """-> {(cmd, workdir): session id} for exec_command runs that were still going when Codex returned and have not
    finished in a write_stdin since."""
    calls, running = {}, {}
    for it in items if isinstance(items, list) else []:
        if it.get("type") == "function_call":
            calls[it["call_id"]] = it
        elif it.get("type") == "function_call_output" and it.get("call_id") in calls:
            call, out = calls[it["call_id"]], it.get("output")
            head = (out if isinstance(out, str) else api._text(_parts(out))).partition("Output:")[0]
            now = re.search(r"^Process running with session ID (\d+)", head, re.M)
            args = _args(call.get("arguments"))
            if call.get("name") == "exec_command" and now and args:
                running[(args.get("cmd"), args.get("workdir"))] = int(now.group(1))
            elif call.get("name") == "write_stdin" and not now and args:
                running = {k: v for k, v in running.items() if v != args.get("session_id")}
    return running


def _args(arguments):
    try:
        args = json.loads(arguments or "{}")
    except ValueError:
        return None
    return args if isinstance(args, dict) else None


def _call(call, names, running=None):
    ns, name = names.get(call["function"]["name"], (None, call["function"]["name"]))
    arguments = _unescalated(call["function"].get("arguments") or "{}")
    args = _args(arguments) if name == "exec_command" and not ns and running and "write_stdin" in names else None
    if args and (args.get("cmd"), args.get("workdir")) in running:
        # Codex returns a command after 10 s with a session id; qwen3.5-4b started the same IDA search again three
        # times instead of waiting on it, so the repeat waits for the first run
        name, arguments = "write_stdin", json.dumps({"session_id": running[(args.get("cmd"), args.get("workdir"))],
                                                     "yield_time_ms": 30000})
    item = {"type": "function_call", "id": f"fc_{uuid.uuid4().hex[:24]}", "call_id": call["id"], "name": name,
            "arguments": arguments, "status": "completed"}
    return dict(item, namespace=ns) if ns else item


def _unescalated(arguments):
    """Codex refuses a call with justification or prefix_rule unless sandbox_permissions is require_escalated, without
    running it, and qwen3.5-4b sent the refused call again 132 times. Both only word an escalation, so they go."""
    try:
        args = json.loads(arguments)
    except ValueError:
        return arguments
    if not isinstance(args, dict) or args.get("sandbox_permissions") == "require_escalated" \
            or not {"justification", "prefix_rule"} & args.keys():
        return arguments
    return json.dumps({k: v for k, v in args.items() if k not in ("justification", "prefix_rule")}, ensure_ascii=False)


def usage(state):
    u = api.usage(state)
    return {"input_tokens": u["prompt_tokens"], "output_tokens": u["completion_tokens"], "total_tokens": u["total_tokens"],
            "input_tokens_details": {"cached_tokens": (u.get("prompt_tokens_details") or {}).get("cached_tokens", 0)},
            "output_tokens_details": {"reasoning_tokens": 0}}


def make_route(cfg, default_profile=None):
    imgdir = cfg["_root"] / "runs" / "_api_images"

    async def responses(request):
        body = await request.json()
        model = body.get("model") or ""
        profile = api.MODELS.get(model) or (model[6:] if model.startswith("lobes/") else default_profile or cfg["profile"])
        if profile not in cfg["profiles"]:
            return JSONResponse({"error": {"message": f"unknown model {model}"}}, status_code=404)
        tools, names = _tools(body.get("tools"))
        running = _running(body.get("input"))
        messages, goal, images = api._messages(_messages(body), imgdir)
        if goal is None:
            return JSONResponse({"error": {"message": "input needs a user message"}}, status_code=400)
        effort = (body.get("reasoning") or {}).get("effort")
        c = dict(cfg, effort=effort) if effort in EFFORT or effort in ALIASES else cfg     # codex also sends minimal
        kw = dict(profile=profile, images=images, messages=messages, client_tools=tools if "tools" in body else None)
        head = {"id": f"resp_{uuid.uuid4().hex[:24]}", "object": "response", "created_at": int(time.time()),
                "model": model or f"lobes/{profile}", "status": "in_progress", "output": []}

        if not body.get("stream"):
            try:
                state = await run_in_threadpool(api.run, c, goal, **kw)
            except Exception as e:      # noqa: BLE001
                status, error = api.failure(e)
                return JSONResponse({"error": error}, status_code=status)
            text = [{"type": "message", "id": f"msg_{uuid.uuid4().hex[:24]}", "role": "assistant", "status": "completed",
                     "content": [{"type": "output_text", "text": state.answer, "annotations": []}]}] if state.answer else []
            return JSONResponse(dict(head, status="completed", usage=usage(state),
                                     output=text + [_call(call, names, running) for call in state.tool_calls]))

        loop, queue, cancel = asyncio.get_running_loop(), asyncio.Queue(), threading.Event()

        def on_delta(kind, text):
            loop.call_soon_threadsafe(queue.put_nowait, (kind, text))

        async def work():
            try:
                return await run_in_threadpool(api.run, c, goal, on_delta=on_delta, cancel=cancel, **kw)
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        def event(kind, **fields):
            return f"event: {kind}\ndata: {json.dumps(dict(type=kind, **fields), ensure_ascii=False)}\n\n"

        async def sse():
            try:
                async for e in events():
                    yield e
            finally:
                cancel.set()        # uvicorn cancels the stream when the client disconnects

        async def events():
            # Codex 0.155 never draws a reasoning item, only its last line as the status label, and it sends every
            # delta to the item added last. So the thinking goes out as commentary messages, which it draws as text,
            # each closed before the next item. Relay steps become closed reasoning items for the label and head the
            # thinking after them. Codex replaces an item with its done copy, so a message learns its phase at the end:
            # final_answer for the answer, which folds the work above it, commentary for text before tool calls
            task = asyncio.ensure_future(work())
            yield event("response.created", response=head)
            output, live, steps, held = [], [], [], []   # live: [item, kind, text sent, text as written]

            def add(item):
                output.append(item)
                return event("response.output_item.added", output_index=len(output) - 1, item=item)

            def message(kind, phase="commentary"):
                live[:] = [{"type": "message", "id": f"msg_{uuid.uuid4().hex[:24]}", "role": "assistant", "status": "in_progress",
                            "content": [], "phase": phase}, kind, "", ""]
                return add(live[0])

            def delta(text):
                live[3] += text
                if live[1] == "reasoning":      # thinking drawn like the answer could not be told apart: it goes as a quote
                    text = ("" if live[2] else "> ") + text.replace("\n", "\n> ")
                live[2] += text
                return event("response.output_text.delta", item_id=live[0]["id"], output_index=len(output) - 1, content_index=0, delta=text)

            def close(text=None, phase=None):
                output[-1] = dict(live[0], status="completed", phase=phase or live[0]["phase"],
                                  content=[{"type": "output_text", "text": live[2] if text is None else text, "annotations": []}])
                live.clear()
                return event("response.output_item.done", output_index=len(output) - 1, item=output[-1])

            def step(text):
                item = {"type": "reasoning", "id": f"rs_{uuid.uuid4().hex[:24]}", "summary": []}
                added = add(item)
                output[-1] = dict(item, summary=[{"type": "summary_text", "text": text}])
                return [added, event("response.reasoning_summary_text.delta", item_id=item["id"], output_index=len(output) - 1,
                                     summary_index=0, delta=text),
                        event("response.output_item.done", output_index=len(output) - 1, item=output[-1])]

            try:
                while (got := await queue.get()) is not None:
                    kind, text = got
                    if not text.strip() and (not live or kind != live[1] or held):
                        continue
                    if kind == "step" and live and live[1] == "content":
                        held.append(text.strip())       # a tool step between replies, or a note after the answer
                        continue
                    if live and (kind != live[1] or held):
                        yield close()
                    for s in held + ([text.strip()] if kind == "step" else []):
                        steps.append(s)
                        for e in step(s):
                            yield e
                    held.clear()
                    if kind == "step":
                        continue
                    if not live:
                        yield message(kind)
                        if kind == "reasoning" and steps:   # so a review's "OK" does not stand alone
                            yield delta("\n".join(steps) + "\n")
                            live[3] = ""
                        steps.clear()
                        text = text.lstrip()
                    yield delta(text)
                state = await task
            except Exception as e:  # noqa: BLE001 - the client gets the error instead of retrying a dropped stream
                # codex retries a server_error with the same request; a full context it reports and compacts instead
                error = api.failure(e)[1]
                yield event("response.failed", response=dict(head, status="failed", error={
                    "code": error["code"] or "server_error", "message": error["message"]}))
                return
            answer, phase = state.answer or "", "commentary" if state.tool_calls else "final_answer"
            if live and live[1] == "reasoning" and answer.strip() and \
                    live[3].strip().removesuffix(providers.BUDGET_MESSAGE.strip()).rstrip() == answer.strip():
                yield close(answer, phase)      # reasoning.py made the thought the answer: shown once, as the answer
                answer = ""
            if live and live[1] == "reasoning":
                yield close()
            if live:                # a cap mid-answer leaves no draft, and the text already sent beats the fallback
                yield close(answer if state.draft is not None and not state.stopped else live[2],
                            "commentary" if state.stopped else phase)
                answer = answer if state.stopped else ""     # why the relay stopped comes after the text sent
            if answer:              # a reviewed answer comes at the end: typed out as a streamed one would be, in ~1.5 s
                yield message("content", phase)
                size = len(answer) if state.tool_calls else max(3, len(answer) // 50)    # a tool call does not wait
                for i in range(0, len(answer), size):
                    yield delta(answer[i:i + size])
                    await asyncio.sleep(0.02)
                yield close()
            for call in state.tool_calls:
                item = _call(call, names, running)
                yield event("response.output_item.added", output_index=len(output), item=item)
                yield event("response.output_item.done", output_index=len(output), item=item)
                output.append(item)
            yield event("response.completed", response=dict(head, status="completed", output=output, usage=usage(state)))
        return StreamingResponse(sse(), media_type="text/event-stream")

    return responses
