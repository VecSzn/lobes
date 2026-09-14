import os
import re
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _expand(v):
    if isinstance(v, str):
        return re.sub(r"\$\{(\w+)\}", lambda m: os.environ.get(m.group(1), ""), v)
    if isinstance(v, dict):
        return {k: _expand(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_expand(x) for x in v]
    return v


def load(path=None):
    p = Path(path) if path else ROOT / "lobes.yaml"
    cfg = _expand(yaml.safe_load(p.read_text(encoding="utf-8")))
    cfg["_root"] = ROOT
    return cfg


def lobe(cfg, name, profile=None):
    """-> ("local", "qwen3.5-4b") for a model, or ("impl", "passthrough") for code."""
    spec = cfg["profiles"][profile or cfg["profile"]][name]
    if "/" in spec:
        prov, model = spec.split("/", 1)
        return prov, model
    return "impl", spec
