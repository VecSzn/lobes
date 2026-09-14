"""Perception: turns images into an observation the text-only lobes can use."""
import json

from ..schema import Observation

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["description", "text", "details"],
          "properties": {"description": {"type": "string"},
                         "text": {"type": "string", "description": "every piece of readable text, verbatim"},
                         "details": {"type": "array", "items": {"type": "string"}}}}


def look(ctx, state, images=None):
    prompt = ("Describe this image for someone who cannot see it, copy out all readable text exactly, and list "
              f"details that matter for this task: {state.goal}")
    r = ctx.chat(state, "perception", [{"role": "user", "content": prompt}], schema=SCHEMA,
                 images=images or state.images, thinking=False, max_tokens=1000)
    d = r.data or {"description": r.text, "text": "", "details": []}
    ref = f"perception_{sum(o.source == 'lobe:perception' for o in state.observations)}"
    (ctx.rundir / f"{ref}.json").write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    summary = d["description"] + (f"\nText in image: {d['text']}" if d["text"] else "") + \
        ("".join(f"\n- {x}" for x in d["details"]) if d["details"] else "")
    state.observations.append(Observation(source="lobe:perception", ref=ref, summary=summary[:3000]))
    return d
