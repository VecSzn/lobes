"""Perception: turns images into observations the text-only lobes can use. Two readers: the vision model
describes and transcribes, and an ocr engine (code, optional) transcribes on its own; they land as separate
observations, each labelled with its source."""
import json

from ..schema import Observation

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["description", "text", "details"],
          "properties": {"description": {"type": "string"},
                         "text": {"type": "string", "description": "every piece of readable text, verbatim"},
                         "details": {"type": "array", "items": {"type": "string"}}}}
ANSWER = {"type": "object", "additionalProperties": False, "required": ["answer"], "properties": {"answer": {"type": "string"}}}
_engine = None


def ocr(path):
    """Lines RapidOCR reads, top to bottom, or None without `pip install lobes[ocr]`."""
    global _engine
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError:
        return None
    _engine = _engine or RapidOCR()
    found, _ = _engine(str(path))
    return [text for _, text, score in found or [] if score >= 0.5]


def look(ctx, state, images=None):
    images = images or state.images
    prompt = ("Describe this image for someone who cannot see it, copy out all readable text exactly, and list "
              f"details that matter for this task: {state.goal}")
    r = ctx.chat(state, "perception", [{"role": "user", "content": prompt}], schema=SCHEMA,
                 images=images, thinking=False, max_tokens=1000)
    d = r.data or {"description": r.text, "text": "", "details": []}
    ref = f"perception_{sum(o.source == 'lobe:perception' for o in state.observations)}"
    (ctx.rundir / f"{ref}.json").write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    summary = d["description"] + (f"\nText in image: {d['text']}" if d["text"] else "") + \
        ("".join(f"\n- {x}" for x in d["details"]) if d["details"] else "")
    state.observations.append(Observation(source="lobe:perception", ref=ref, summary=summary[:3000]))
    for img in images:
        lines = ocr(img)
        if lines is None:
            break
        # registered like a tool run so claims can cite it and the verifier can match answers against it
        ref = f"ocr_{sum(o.source == 'tool:ocr' for o in state.observations)}"
        text = "\n".join(lines)
        state.tool_results[ref] = {"stdout": text, "exit": 0}
        state.observations.append(Observation(source="tool:ocr", ref=ref, summary=("Text the ocr engine read, top to bottom:\n" + text) if text else "(the ocr engine found no text)"))
        ctx.trace.write("tool", ref=ref, call={"name": "ocr", "args": {"image": str(img)}}, exit=0, summary=text[:500])
    return d


def ask(ctx, state):
    """The perception witness: answers the question from the image directly. Not written to the observations,
    so the reasoning witness reads the description and the ocr lines without knowing this answer."""
    from . import Witness
    r = ctx.chat(state, "perception", [{"role": "user", "content": f"Look at the image and answer with only the answer, nothing else: {state.goal}"}],
                 schema=ANSWER, images=state.images, thinking=False, max_tokens=300)
    return Witness("perception", (r.data or {}).get("answer", r.text).strip() or None)
