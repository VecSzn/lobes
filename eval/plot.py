"""The README charts, from eval/results. `python eval/plot.py` rewrites docs/img/*.svg."""
import statistics
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from lobes.eval import jsonl

ROOT = Path(__file__).resolve().parent.parent
IMG = ROOT / "docs" / "img"
# items per suite; a suite is drawn only once a run has all of them. tools and multistep grew to 60 in
# PREREG-v4, so a v3 run at 30 and a v4 run at 60 are both complete.
N = {"gsm8k": (200,), "humaneval": (30,), "tools": (30, 60), "multistep": (30, 60), "simpleqa": (30,), "ocrbench": (50,)}
# label, results directory, condition. The 9B has no ocrbench: it takes no images. A missing file is skipped.
SERIES = [("bare 9B", "5090-v3", "R"),
          ("Lobes, medium", "5090-v3-witness", "D"),
          ("Lobes, medium again", "5090-v3-witness-2", "D"),   # same code, same box: the error bar
          ("Lobes, high", "5090-v3-witness-high", "D")]
COLORS = ["#7a7a7a", "#4c72b0", "#8fb0d8", "#dd8452"]
# the 30 harder items PREREG-v4 added to each of these two; the ids below them are the v3 half.
V4 = {"tools": 50, "multistep": 40}
V4_SERIES = [("bare 9B", "5090-v4", "R"),
             ("Lobes, medium", "5090-v4", "D"),
             ("Lobes, medium again", "5090-v4-2", "D"),
             ("Lobes, high", "5090-v4-high", "D")]


def load(tag, cond):
    p = ROOT / "eval" / "results" / tag / f"{cond}-s0.jsonl"
    return [r for r in jsonl(p) if "error" not in r] if p.exists() else []


def done(tag, cond, n):
    """suite -> records, only the suites the run has finished"""
    recs = load(tag, cond)
    out = {s: [r for r in recs if r["suite"] == s] for s in n}
    return {s: rs for s, rs in out.items() if len(rs) in n[s]}


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


def harder():
    fig, ax = plt.subplots(figsize=(6, 3.4))
    runs = [(label, {s: [r for r in load(tag, cond)
                         if r["suite"] == s and int(r["id"].split("-")[1]) >= first and "error" not in r]
                     for s, first in V4.items()}) for label, tag, cond in V4_SERIES]
    runs = [(label, d) for label, d in runs if all(len(rs) == 30 for rs in d.values())]
    w = 0.8 / len(runs)
    for i, (label, d) in enumerate(runs):
        for j, s in enumerate(V4):
            x = j + (i - (len(runs) - 1) / 2) * w
            ax.bar(x, acc(d[s]), w, color=COLORS[i], label=label)
            ax.text(x, acc(d[s]) + 1, count(d[s]), ha="center", va="bottom", rotation=90, fontsize=6.5)
            label = None
    ax.set_xticks(range(len(V4)), [f"{s}, the 30 harder" for s in V4])
    ax.set_ylim(0, 128)
    ax.set_yticks(range(0, 101, 20))
    ax.set_ylabel("correct %  (higher is better)")
    ax.legend(frameon=False, ncol=2, loc="lower center", bbox_to_anchor=(0.5, 1.0), fontsize=8)
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


if __name__ == "__main__":
    IMG.mkdir(parents=True, exist_ok=True)
    for name, f in (("suites", suites), ("cost", cost), ("harder", harder)):
        fig = f()
        fig.savefig(IMG / f"{name}.svg", bbox_inches="tight")
        fig.savefig(IMG / f"{name}.png", dpi=110, bbox_inches="tight")
        print(name, [p.stat().st_size for p in (IMG / f"{name}.svg", IMG / f"{name}.png")])
