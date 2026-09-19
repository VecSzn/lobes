"""BFCL v4 AST categories, scored by the official checker vendored in bfcl_official/.

Only the four live (user-contributed) AST categories run here. The executable categories
need live third-party APIs, and relevance/irrelevance and multi-turn score a different
thing than "did it compose the right call".
"""
import json
from pathlib import Path

from .bfcl_official import (
    DEFAULT_SYSTEM_PROMPT_FORMAT,
    Language,
    ReturnFormat,
    _func_doc_language_specific_pre_processing,
    ast_checker,
    default_decode_ast_prompting,
    formulate_system_prompt,
)

BUDGET = 500
WHOLE = ("live_simple", "live_parallel", "live_parallel_multiple")   # 258 + 16 + 24
FILL = "live_multiple"          # 1053 upstream, truncated in file order to reach BUDGET
CATEGORIES = WHOLE + (FILL,)
# Any name works: the vendored MODEL_CONFIG_MAPPING answers underscore_to_dot=False for
# everything, which is right for a prompt-mode model that sees function names verbatim.
MODEL = "lobes"


def _read(data_dir, name):
    return [json.loads(l) for l in (data_dir / name).read_text(encoding="utf-8").split("\n") if l.strip()]


def _prompt(entry, category):
    """Official prompt mode: BFCL system prompt plus the turn, flattened to one string."""
    functions = _func_doc_language_specific_pre_processing(entry["function"], category)
    system = formulate_system_prompt(DEFAULT_SYSTEM_PROMPT_FORMAT, functions)
    turn = entry["question"][0]
    # A dataset system message is appended after the BFCL one, as upstream does.
    parts = [system] + [m["content"] for m in turn if m["role"] == "system"]
    parts += [m["content"] for m in turn if m["role"] != "system"]
    return functions, "\n\n".join(parts)


def load(data_dir):
    data_dir = Path(data_dir)
    items = []
    for category in CATEGORIES:
        entries = _read(data_dir, f"bfcl_{category}.json")
        answers = {a["id"]: a["ground_truth"] for a in _read(data_dir, f"bfcl_{category}_answer.json")}
        if category == FILL:
            entries = entries[:max(0, BUDGET - len(items))]
        for e in entries:
            functions, prompt = _prompt(e, category)
            items.append({"id": "bfcl-" + e["id"], "prompt": prompt, "category": category,
                          "function": functions, "ground_truth": answers[e["id"]]})
    return items


def judge(item, answer):
    """-> (correct, abstained). Parse failures are wrong answers, which is how BFCL counts them."""
    try:
        decoded = default_decode_ast_prompting(answer or "", ReturnFormat.PYTHON)
        result = ast_checker(item["function"], decoded, item["ground_truth"],
                             Language.PYTHON, item["category"], MODEL)
    except Exception:
        return False, False
    return bool(result["valid"]), False


# --------------------------------------------------------------------------- self-check

def _concrete(value):
    """Ground truth nests alternatives inside dicts too; keep the first one at every level.
    A dict value that is not a list is already a literal (dict_checker compares those whole)."""
    if isinstance(value, dict):
        out = {}
        for key, alts in value.items():
            if not isinstance(alts, list):
                out[key] = alts
            elif alts and alts[0] != "":
                out[key] = _concrete(alts[0])
        return out
    if isinstance(value, list):
        return [_concrete(v) for v in value]
    return value


def _required(item, name):
    return next((f["parameters"].get("required", []) for f in item["function"] if f["name"] == name), [])


def _render(item, rename=False, corrupt=False):
    """The first acceptable answer, written the way the prompt asks the model to write it."""
    calls = []
    for call in item["ground_truth"]:
        name, params = next(iter(call.items()))
        required = _required(item, name)
        args = {}
        for param, alts in params.items():
            # "" marks an optional the answer omits; a required one still has to be written.
            if param in required:
                alts = [a for a in alts if a != ""]
            if not alts or alts[0] == "":
                continue
            args[param] = _concrete(alts[0])
        if corrupt and args:
            target = next((p for p in required if p in args), sorted(args)[0])
            args[target] = "__lobes_never_an_accepted_value__"
        body = ", ".join(f"{k}={v!r}" for k, v in args.items())
        calls.append(f"{name + '_xyzzy' if rename else name}({body})")
    return "[" + ", ".join(calls) + "]"


def _main():
    items = load(Path(__file__).resolve().parents[2] / "eval" / "data")
    counts = {c: sum(i["category"] == c for i in items) for c in CATEGORIES}
    print(f"loaded {len(items)} items")
    for c, n in counts.items():
        print(f"  {c:24s} {n}")

    bad = [i["id"] for i in items if not judge(i, _render(i))[0]]
    ok = len(items) - len(bad)
    print(f"\n1. gold answers accepted: {ok}/{len(items)} = {ok / len(items):.1%}")
    for i in bad[:20]:
        print("   FAIL", i)
    if len(bad) > 20:
        print(f"   ... and {len(bad) - 20} more")

    renamed = sum(judge(i, _render(i, rename=True))[0] for i in items)
    print(f"2a. wrong function name accepted: {renamed}/{len(items)}")

    corruptible = [i for i in items if any(a and a[0] != "" for c in i["ground_truth"]
                                           for a in next(iter(c.values())).values())]
    corrupted = sum(judge(i, _render(i, corrupt=True))[0] for i in corruptible)
    print(f"2b. corrupted argument accepted: {corrupted}/{len(corruptible)}")

    for label, answer in [("empty", ""), ("none", None), ("whitespace", "   \n "),
                          ("prose", "I think you should call the weather API"),
                          ("prose+name", "You should probably use get_user_info for this.")]:
        n = sum(judge(i, answer)[0] for i in items)
        print(f"3. {label:12s} accepted: {n}/{len(items)}")

    assert len(bad) <= len(items) * 0.02, f"gold rejected on {len(bad)} items"
    assert renamed == 0 and corrupted <= len(corruptible) * 0.02
    print("\nself-check ok")


if __name__ == "__main__":
    _main()
