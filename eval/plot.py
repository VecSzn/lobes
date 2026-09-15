"""The README charts, from eval/results. `python eval/plot.py` rewrites docs/img/*.svg."""
import json
import statistics
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent.parent
IMG = ROOT / "docs" / "img"
# items per suite on the enlarged suites (PREREG-v3); a suite is drawn only once a run has all of them
N = {"gsm8k": 200, "humaneval": 30, "tools": 30, "multistep": 30, "simpleqa": 30, "ocrbench": 50}
# label, results directory, condition. The 9B has no ocrbench: it takes no images. A missing file is skipped.
SERIES = [("bare 9B", "5090-v3", "R"),
          ("v3, medium", "5090-v3", "D"),
          ("v3, high", "5090-v3-high", "D"),
          ("v2, high", "5090-v2fix-high", "D")]   # the second version with the v3 intake, branch v2-fix
# the milestones, on the items the 9B runs and against it; v2 medium is the second version as pre-registered
MILE = [("v2, medium", "5090-pre-v3", "D"), ("v2, high", "5090-v2fix-high", "D"),
        ("v3, medium", "5090-v3", "D"), ("v3, high", "5090-v3-high", "D")]
COLORS = ["#7a7a7a", "#4c72b0", "#dd8452", "#55a868", "#8172b3"]


def load(tag, cond):
    p = ROOT / "eval" / "results" / tag / f"{cond}-s0.jsonl"
    return [r for r in map(json.loads, p.read_text(encoding="utf-8").splitlines()) if "error" not in r] if p.exists() else []


def done(tag, cond, n):
    """suite -> records, only the suites the run has finished"""
    recs = load(tag, cond)
    out = {s: [r for r in recs if r["suite"] == s] for s in n}
    return {s: rs for s, rs in out.items() if len(rs) == n[s]}


def acc(rs):
    return 100 * sum(r["correct"] for r in rs) / len(rs)


def count(rs):
    return f"{sum(r['correct'] for r in rs)}/{len(rs)}"


def common(runs):
    """the suites every run has finished, in N order"""
    return [s for s in N if all(s in d for d in runs)]


def suites():
    fig, ax = plt.subplots(figsize=(10, 3.8))
    runs = [(label, done(tag, cond, N)) for label, tag, cond in SERIES]
    runs = [(label, d) for label, d in runs if d]
    w = 0.8 / len(runs)
    for i, (label, d) in enumerate(runs):
        for j, s in enumerate(N):
            if s in d:
                x = j + (i - (len(runs) - 1) / 2) * w
                ax.bar(x, acc(d[s]), w, color=COLORS[i], label=label)
                ax.text(x, acc(d[s]) + 1, count(d[s]), ha="center", va="bottom", rotation=90, fontsize=6.5)
                label = None
    ax.set_xticks(range(len(N)), N)
    ax.set_ylim(0, 128)
    ax.set_yticks(range(0, 101, 20))
    ax.set_ylabel("correct %  (higher is better)")
    ax.legend(frameon=False, ncol=len(runs), loc="lower center", bbox_to_anchor=(0.5, 1.0))
    ax.spines[["top", "right"]].set_visible(False)
    return fig


def cost():
    fig, axes = plt.subplots(1, 2, figsize=(9, 3))
    runs = [(label, done(tag, cond, N)) for label, tag, cond in SERIES]
    runs = [(label, d) for label, d in runs if d]
    shared = common([d for _, d in runs])
    for ax, key, title in ((axes[0], lambda r: r["tokens"].get("total_tokens", 0), "tokens per item"),
                           (axes[1], lambda r: r["ms"] / 1000, "seconds per item")):
        for i, (label, d) in enumerate(runs):
            v = statistics.mean(key(r) for s in shared for r in d[s])
            ax.barh(i, v, color=COLORS[i])
            ax.text(v, i, f" {v:,.0f}", va="center", fontsize=8)
        ax.set_yticks(range(len(runs)), [label for label, _ in runs])
        ax.invert_yaxis()
        ax.set_title(title, fontsize=9)
        ax.set_xlim(0, ax.get_xlim()[1] * 1.15)
        ax.locator_params(axis="x", nbins=6)
        ax.xaxis.set_major_formatter(matplotlib.ticker.StrMethodFormatter("{x:,.0f}"))
        ax.spines[["top", "right"]].set_visible(False)
    fig.suptitle(f"mean over {', '.join(shared)}  (lower is better)", fontsize=8)
    fig.tight_layout()
    return fig


def milestones():
    fig, ax = plt.subplots(figsize=(6.5, 2.6))
    n = {k: v for k, v in N.items() if k != "ocrbench"}
    nine = done("5090-v3", "R", n)
    runs = [(label, done(tag, cond, n)) for label, tag, cond in MILE]
    runs = [(label, d) for label, d in runs if d]
    shared = common([d for _, d in runs] + [nine])
    for i, (label, d) in enumerate(runs):
        rs = [r for s in shared for r in d[s]]
        ax.barh(i, acc(rs), color=COLORS[1 + i])
        ax.text(acc(rs) + 0.5, i, count(rs), va="center", fontsize=8)
    rs = [r for s in shared for r in nine[s]]
    ax.axvline(acc(rs), color=COLORS[0], ls="--")
    ax.text(acc(rs) - 0.5, len(runs) - 0.35, f"bare 9B {count(rs)} ", color=COLORS[0], fontsize=8, ha="right", va="center")
    ax.set_yticks(range(len(runs)), [label for label, _ in runs])
    ax.set_ylim(len(runs) - 0.1, -0.6)
    ax.set_xlim(0, 100)
    ax.set_xlabel(f"correct % on {', '.join(shared)}, four items in flight  (higher is better)", fontsize=8)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    return fig


if __name__ == "__main__":
    IMG.mkdir(parents=True, exist_ok=True)
    for name, f in (("suites", suites), ("cost", cost), ("milestones", milestones)):
        fig = f()
        fig.savefig(IMG / f"{name}.svg", bbox_inches="tight")
        fig.savefig(IMG / f"{name}.png", dpi=110, bbox_inches="tight")
        print(name, [p.stat().st_size for p in (IMG / f"{name}.svg", IMG / f"{name}.png")])
