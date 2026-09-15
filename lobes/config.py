from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def load(path=None):
    p = Path(path) if path else ROOT / "lobes.yaml"
    cfg = yaml.safe_load(p.read_text(encoding="utf-8"))
    cfg["_root"] = ROOT
    return cfg


def lobe(cfg, name, profile=None):
    """-> ("local", "qwen3.5-4b") for a model, or ("impl", "passthrough") for code."""
    spec = cfg["profiles"][profile or cfg["profile"]][name]
    if "/" in spec:
        prov, model = spec.split("/", 1)
        return prov, model
    return "impl", spec
