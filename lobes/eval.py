"""lobes eval: the comparison pre-registered in eval/PREREG.md. One JSONL line per item, resumable."""
import json
import random
import re
import shutil
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx

from . import config
from .lobe.verifier import norm, nums, same
from .runner import effort, run

DATA = config.ROOT / "eval" / "data"
RESULTS = config.ROOT / "eval" / "results"
SHUFFLE_SEED = 20260914
# PREREG numbers were gsm8k 50, simpleqa 50, SMALL 10. The quick timing (A 64 s/item, B 24 s/item) projected
# ~10 h, so the pre-registered cut rule applied: seeds 1-2 multistep only, gsm8k/simpleqa 30, B3 dropped.
N = {"gsm8k": 30, "humaneval": 30, "tools": 20, "simpleqa": 30, "ocrbench": 20, "multistep": 10}
SMALL = 0                     # seeds 1 and 2: first SMALL items of every suite except multistep


def jsonl(path):
    # not splitlines(): a U+2028 inside a model's answer counts as a line break there and cuts the record in two
    return [json.loads(l) for l in path.read_text(encoding="utf-8").split("\n") if l.strip()]


CONDITIONS = {                # cfg overrides on top of the profile; see PREREG for what each one is
    "A":  dict(profile="single-9b", no_escalate=True),
    "B":  dict(profile="single-4b", no_escalate=True),
    "B3": dict(profile="single-4b", no_escalate=True, vote=3),
    "C":  dict(profile="shared", no_escalate=True),
    "D":  dict(profile="specialists", no_escalate=True),
    "E":  dict(profile="specialists"),
    "F":  dict(profile="remote", no_escalate=True),
}
VISION = {"B", "B3", "C", "D", "E"}
ABSTAIN = re.compile(r"don'?t know|do not know|not sure|cannot (find|determine|verify)|no (reliable )?information"
                     r"|unknown|unable to", re.I)
FILES = {
    "gsm8k_test.parquet": "https://huggingface.co/datasets/openai/gsm8k/resolve/main/main/test-00000-of-00001.parquet",
    "humaneval_test.parquet": "https://huggingface.co/datasets/openai/openai_humaneval/resolve/main/openai_humaneval/test-00000-of-00001.parquet",
    "simpleqa_test.csv": "https://openaipublic.blob.core.windows.net/simple-evals/simple_qa_test_set.csv",
    "ocrbench_test.parquet": "https://huggingface.co/datasets/echo840/OCRBench/resolve/main/data/test-00000-of-00001.parquet",
}


def fetch():
    DATA.mkdir(parents=True, exist_ok=True)
    for name, url in FILES.items():
        p = DATA / name
        part = p.with_suffix(p.suffix + ".part")
        if p.exists():
            continue
        have = part.stat().st_size if part.exists() else 0
        headers = {"Range": f"bytes={have}-"} if have else {}
        with httpx.stream("GET", url, headers=headers, follow_redirects=True, timeout=60) as r:
            if r.status_code == 416:            # the part is already complete
                part.rename(p)
                continue
            r.raise_for_status()
            with open(part, "ab" if r.status_code == 206 else "wb") as f:
                for chunk in r.iter_bytes(1 << 20):
                    f.write(chunk)
        part.rename(p)
        print("fetched", name)


def _pick(items, name):
    """Fixed subset: shuffled once with SHUFFLE_SEED, ids recorded next to the data."""
    idx = list(range(len(items)))
    random.Random(SHUFFLE_SEED).shuffle(idx)
    chosen = [items[i] for i in idx[:N[name]]]
    p = DATA / f"{name}.ids.json"
    if not p.exists():
        p.write_text(json.dumps([c["id"] for c in chosen]), encoding="utf-8")
    return chosen


def load_suite(name):
    import pandas as pd
    if name == "gsm8k":
        df = pd.read_parquet(DATA / "gsm8k_test.parquet")
        items = [{"id": f"gsm8k-{i}", "prompt": q, "gold": a.split("####")[-1].strip().replace(",", "")}
                 for i, (q, a) in enumerate(zip(df.question, df.answer))]
    elif name == "humaneval":
        df = pd.read_parquet(DATA / "humaneval_test.parquet")
        items = [{"id": r.task_id.replace("/", "-"), "prompt": "Complete this python function. Reply with the complete "
                  "function, signature and imports included, no explanation.\n\n" + r.prompt,
                  "source": r.prompt, "test": r.test, "entry_point": r.entry_point} for r in df.itertuples()]
    elif name == "simpleqa":
        df = pd.read_csv(DATA / "simpleqa_test.csv")
        items = [{"id": f"simpleqa-{i}", "prompt": q, "gold": a} for i, (q, a) in enumerate(zip(df.problem, df.answer))]
    elif name == "ocrbench":
        df = pd.read_parquet(DATA / "ocrbench_test.parquet")
        items = [{"id": f"ocrbench-{i}", "prompt": r.question, "gold": [str(a) for a in r.answer], "row": i}
                 for i, r in enumerate(df.itertuples())]
        chosen = _pick(items, name)
        (DATA / "ocrbench").mkdir(exist_ok=True)
        for it in chosen:                       # only the chosen images leave the parquet
            img = DATA / "ocrbench" / f"{it['row']}.png"
            if not img.exists():
                img.write_bytes(df.iloc[it["row"]]["image"]["bytes"])
            it["images"] = [str(img)]
        return chosen
    else:                                       # tools, multistep: mine, small, all of them
        return jsonl(config.ROOT / "eval" / "suites" / f"{name}.jsonl")
    return _pick(items, name)


def judge(suite, item, answer):
    """-> (correct, abstained). Judges are fixed in PREREG."""
    answer = answer or ""
    if suite == "gsm8k":
        return same(answer, item["gold"]), False
    if suite == "humaneval":
        code = re.sub(r"^\s*```\w*\n|\n```\s*$", "", answer.rstrip())   # keep the indentation of body-only answers
        src = code if f"def {item['entry_point']}" in code else item["source"] + code
        try:
            r = subprocess.run([sys.executable, "-c", src + "\n\n" + item["test"] + f"\ncheck({item['entry_point']})"],
                               capture_output=True, timeout=10)
            return r.returncode == 0, False
        except subprocess.TimeoutExpired:
            return False, False
    if suite == "tools":
        g = item["answer"]
        ok = same(answer, g) if re.fullmatch(r"-?[\d.]+", g) else norm(g) in norm(answer)
        return ok, False
    if suite == "simpleqa":
        return norm(item["gold"]) in norm(answer), bool(ABSTAIN.search(answer))
    if suite == "ocrbench":
        return any(norm(g) in norm(answer) for g in item["gold"]), False
    if suite == "multistep":
        return all(norm(x) in norm(answer) for x in item["answers"]), False
    raise KeyError(suite)


class Vram(threading.Thread):
    """Peak nvidia-smi memory.used, sampled every 100 ms by one long-lived nvidia-smi."""
    def __init__(self):
        super().__init__(daemon=True)
        self.peak = 0

    def run(self):
        try:
            p = subprocess.Popen(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits", "-lms", "100"],
                                 stdout=subprocess.PIPE, text=True)
        except OSError:
            return
        for line in p.stdout:
            if line.strip().isdigit():
                self.peak = max(self.peak, int(line))


def run_item(cfg, cond, seed, suite, item, vram, tag=""):
    c = dict(cfg, seed=seed, **{k: v for k, v in CONDITIONS[cond].items() if k != "profile"})
    task_id = f"eval-{tag + '-' if tag else ''}{cond}-s{seed}-{item['id']}"   # tagged runs keep their own traces
    shutil.rmtree(cfg["_root"] / "runs" / task_id, ignore_errors=True)
    vram.peak = 0
    t0 = time.perf_counter()
    rec = {"cond": cond, "seed": seed, "suite": suite, "id": item["id"], "task_id": task_id, "effort": c.get("effort") or "medium"}
    try:
        st = run(c, item["prompt"], profile=CONDITIONS[cond]["profile"], images=item.get("images"), task_id=task_id)
    except Exception as e:                      # one broken item must not kill the night
        rec.update(error=repr(e)[:500], ms=int((time.perf_counter() - t0) * 1000), correct=False, abstained=False)
        return rec
    correct, abstained = judge(suite, item, st.answer)
    lobe_ms = {}
    for lobe, _, ms, _ in st.calls:
        lobe_ms[lobe] = lobe_ms.get(lobe, 0) + ms
    swap_ms = 0
    for r in jsonl(cfg["_root"] / "runs" / task_id / "trace.jsonl"):
        if r["kind"] == "model":
            swap_ms += r["ms"]
    last = st.verdicts[-1] if st.verdicts else None
    rec.update(answer=(st.answer or "")[:1000], correct=correct, abstained=abstained, task_class=st.task_class,
               tokens=st.usage, ms=st.ms(), lobe_ms=lobe_ms, swaps=st.swaps, swap_ms=swap_ms, vram_peak_mb=vram.peak,
               steps=st.steps, retries=st.retries, escalations=st.escalations, calls=len(st.calls),
               basis=last.basis if last else None, passed=bool(last and last.verdict == "PASS"),
               stuck=st.steps >= effort(c)["steps"] and not (last and last.verdict == "PASS"))
    if suite in ("gsm8k", "tools"):             # lenient twin of the strict judge, reported next to it
        rec["gold_in_answer"] = item.get("gold", item.get("answer")).replace(",", "") in nums(st.answer or "")
    return rec


def plan(cond, seed, quick, suites=None):
    for suite in suites or N:
        if suite == "ocrbench" and cond not in VISION:
            continue
        n = 3 if quick else N[suite] if seed == 0 or suite == "multistep" else SMALL
        yield suite, load_suite(suite)[:n]


def main(cfg, conditions, seeds, quick=False, suites=None, tag=""):
    sys.stdout.reconfigure(errors="replace")  # windows console is gbk; an umlaut in an answer killed a run
    fetch()
    results = RESULTS / tag                   # a tag keeps one code version's run apart from another's
    results.mkdir(parents=True, exist_ok=True)
    vram = Vram()
    vram.start()
    for cond in conditions:
        prov, _ = config.lobe(cfg, "reasoning", CONDITIONS[cond]["profile"])
        p = cfg["providers"][prov]
        if p.get("base_url", "").startswith("https://") and not p.get("api_key"):
            print(f"{cond}: skipped, {prov} has no api key")
            continue
        for seed in seeds:
            out = results / f"{'quick-' if quick else ''}{cond}-s{seed}.jsonl"
            done = set()
            if out.exists():
                done = {(r["suite"], r["id"]) for r in jsonl(out)}
            for suite, items in plan(cond, seed, quick, suites):
                for item in items:
                    if (suite, item["id"]) in done:
                        continue
                    rec = run_item(cfg, cond, seed, suite, item, vram, tag)
                    with open(out, "a", encoding="utf-8") as f:
                        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    print(f"{cond} s{seed} {suite} {item['id']}: {'ok' if rec['correct'] else 'x '} {rec['ms']} ms "
                          f"{rec.get('tokens', {}).get('total_tokens', 0)} tok {rec.get('answer', rec.get('error', ''))[:60]!r}", flush=True)


def report(quick=False, tag=""):
    """Markdown tables from eval/results; the narrative in REPORT.md is written by hand."""
    recs = []
    for p in sorted((RESULTS / tag).glob(f"{'quick-' if quick else ''}[A-F]*-s*.jsonl")):
        recs += jsonl(p)
    recs = [r for r in recs if "error" not in r]
    conds = [c for c in CONDITIONS if any(r["cond"] == c for r in recs)]
    out = []

    def cell(rs, f):
        return f"{f(rs):.2f}" if rs else "–"

    def table(title, f, suites=N):
        out.append(f"\n### {title}\n\n| suite | " + " | ".join(conds) + " |\n|---|" + "---|" * len(conds))
        for s in suites:
            row = [cell([r for r in recs if r["cond"] == c and r["suite"] == s and r["seed"] == 0], f) for c in conds]
            out.append(f"| {s} | " + " | ".join(row) + " |")

    acc = lambda rs: 100 * sum(r["correct"] for r in rs) / len(rs)
    tok = lambda rs: statistics.mean(r["tokens"].get("total_tokens", 0) for r in rs)
    sec = lambda rs: statistics.mean(r["ms"] for r in rs) / 1000
    table("accuracy % (seed 0)", acc)
    table("accuracy per 1k tokens", lambda rs: acc(rs) / (tok(rs) / 1000))
    table("accuracy per second", lambda rs: acc(rs) / sec(rs))
    table("mean tokens", tok)
    table("mean seconds", sec)
    table("mean swaps", lambda rs: statistics.mean(r["swaps"] for r in rs))
    table("stuck loop %", lambda rs: 100 * sum(r["stuck"] for r in rs) / len(rs))
    table("VRAM peak MB (max over items, includes the desktop)", lambda rs: max(r["vram_peak_mb"] for r in rs))
    table("simpleqa: abstained %", lambda rs: 100 * sum(r["abstained"] for r in rs) / len(rs), ["simpleqa"])
    table("simpleqa: confident correct % (correct and not abstained)",   # v2 hedges; the v1 judge alone would credit a hedged right guess
          lambda rs: 100 * sum(r["correct"] and not r["abstained"] for r in rs) / len(rs), ["simpleqa"])
    table("simpleqa: empty answer %", lambda rs: 100 * sum(not r["answer"].strip() for r in rs) / len(rs), ["simpleqa"])
    table("simpleqa: unsupported % (answered, not abstained, wrong)",
          lambda rs: 100 * sum(bool(r["answer"].strip()) and not r["abstained"] and not r["correct"] for r in rs) / len(rs),
          ["simpleqa"])
    table("lenient: gold number anywhere in the answer %",  # or-ed with strict: "42.0" vs gold "42" misses the string test
          lambda rs: 100 * sum(r["correct"] or r.get("gold_in_answer", False) for r in rs) / len(rs), ["gsm8k", "tools"])
    out.append("\n### multistep across seeds: accuracy % per seed, mean, std\n\n| cond | s0 | s1 | s2 | mean | std |\n|---|---|---|---|---|---|")
    for c in conds:
        per = []
        for s in (0, 1, 2):
            rs = [r for r in recs if r["cond"] == c and r["suite"] == "multistep" and r["seed"] == s]
            per.append(acc(rs) if rs else None)
        have = [x for x in per if x is not None]
        out.append(f"| {c} | " + " | ".join("–" if x is None else f"{x:.0f}" for x in per) +
                   f" | {statistics.mean(have):.1f} | {statistics.pstdev(have):.1f} |" if have else f"| {c} | – | – | – | – | – |")
    out.append("\n### escalation use (E): items that reached the 9B, and their accuracy\n\n| suite | escalated | correct of those |\n|---|---|---|")
    for s in N:
        rs = [r for r in recs if r["cond"] == "E" and r["suite"] == s and r["seed"] == 0 and r.get("escalations")]
        out.append(f"| {s} | {len(rs)} | {sum(r['correct'] for r in rs)} |")
    return "\n".join(out)


if __name__ == "__main__":
    assert judge("gsm8k", {"gold": "18"}, "She makes $18")[0] and not judge("gsm8k", {"gold": "18"}, "$18 a day, 9 * 2")[0]
    assert judge("tools", {"answer": "Monday"}, "It is a Monday.")[0] and judge("tools", {"answer": "391"}, "391")[0]
    he = {"entry_point": "add", "source": "def add(a, b):\n", "test": "def check(c):\n    assert c(1, 2) == 3\n"}
    assert judge("humaneval", he, "```python\ndef add(a, b):\n    return a + b\n```")[0] and judge("humaneval", he, "    return a + b\n")[0]
    assert not judge("humaneval", he, "def add(a, b):\n    return a - b")[0]
    assert judge("simpleqa", {"gold": "Michio Sugeno"}, "I don't know, maybe Michio Sugeno.") == (True, True)
    assert judge("multistep", {"answers": ["210", "bob"]}, "sum is 210, best is Bob")[0]
    assert not judge("ocrbench", {"gold": ["CENTRE"]}, "center")[0] and judge("ocrbench", {"gold": ["CENTRE"]}, "It says CENTRE")[0]
    print("eval judges ok")
