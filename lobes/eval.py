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
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

from . import config, providers
from .lobe.verifier import norm, nums, same
from .models import ModelManager
from .runner import EFFORT, run

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
    "R":  dict(profile="single-9b", raw=True),      # the 9B as shipped: one chat call, no lobes, no tools
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
        if c.get("raw"):
            return raw_item(c, cond, suite, item, vram, rec)
        st = run(c, item["prompt"], profile=CONDITIONS[cond]["profile"], images=item.get("images"), task_id=task_id)
    except Exception as e:                      # one broken item must not kill the night
        rec.update(error=repr(e)[:500], ms=int((time.perf_counter() - t0) * 1000), correct=False, abstained=False)
        return rec
    correct, abstained = judge(suite, item, st.answer)
    lobe_ms = {}
    for lobe, _, ms, _ in st.calls:
        lobe_ms[lobe] = lobe_ms.get(lobe, 0) + ms
    swap_ms = forced = 0
    for r in jsonl(cfg["_root"] / "runs" / task_id / "trace.jsonl"):
        if r["kind"] == "model":
            swap_ms += r["ms"]
        forced += r["kind"] == "call" and bool(r.get("forced"))
    last = st.verdicts[-1] if st.verdicts else None
    rec.update(answer=(st.answer or "")[:1000], correct=correct, abstained=abstained, task_class=st.task_class,
               tokens=st.usage, ms=st.ms(), lobe_ms=lobe_ms, swaps=st.swaps, swap_ms=swap_ms, vram_peak_mb=vram.peak,
               steps=st.steps, retries=st.retries, escalations=st.escalations, calls=len(st.calls),
               basis=last.basis if last else None, passed=bool(last and last.verdict == "PASS"), level=st.effort,
               stuck=st.steps >= EFFORT[st.effort]["steps"] and not (last and last.verdict == "PASS"), forced=forced)
    if suite in ("gsm8k", "tools"):             # lenient twin of the strict judge, reported next to it
        rec["gold_in_answer"] = item.get("gold", item.get("answer")).replace(",", "") in nums(st.answer or "")
    return rec


RAW_TAIL = "\n\nEnd your reply with the final answer alone on the last line."


def raw_item(cfg, cond, suite, item, vram, rec):
    """The model on its own: one chat call at the runner's cold-sample temperature and seed, thinking left at the
    template default, no tools, no images. The judges read free text, so the prompt asks for the answer last."""
    t0 = time.perf_counter()
    prov, model = config.lobe(cfg, "reasoning", CONDITIONS[cond]["profile"])
    mm = ModelManager(cfg)
    mm.ensure(model)
    r = providers.chat(cfg["providers"][prov], model, [{"role": "user", "content": item["prompt"] + RAW_TAIL}],
                       temperature=0.2, max_tokens=12000, seed=cfg.get("seed"))
    answer = r.text.strip()
    if suite == "humaneval":
        blocks = re.findall(r"```\w*\n(.*?)```", answer, re.S)   # the judge runs the answer as code
        answer = blocks[-1] if blocks else answer
    correct, abstained = judge(suite, item, answer)
    rec.update(answer=answer[:1000], correct=correct, abstained=abstained, task_class="raw", tokens=r.usage,
               ms=int((time.perf_counter() - t0) * 1000), lobe_ms={"raw": r.ms},
               swaps=sum(op == "load" for _, op, _, _ in mm.events), swap_ms=sum(ms for _, op, ms, _ in mm.events),
               vram_peak_mb=vram.peak, steps=1, retries=0, escalations=0, calls=1, basis=None, passed=None,
               stuck=False, finish=r.finish, forced=int(r.forced))
    if suite in ("gsm8k", "tools"):
        rec["gold_in_answer"] = item.get("gold", item.get("answer")).replace(",", "") in nums(answer)
    return rec


def plan(cond, seed, quick, suites=None):
    for suite in suites or N:
        if suite == "ocrbench" and cond not in VISION:
            continue
        n = 3 if quick else N[suite] if seed == 0 or suite == "multistep" else SMALL
        yield suite, load_suite(suite)[:n]


def main(cfg, conditions, seeds, quick=False, suites=None, tag="", workers=1):
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
            todo = [(suite, item) for suite, items in plan(cond, seed, quick, suites) for item in items
                    if (suite, item["id"]) not in done]
            lock = threading.Lock()

            def one(suite, item):
                rec = run_item(cfg, cond, seed, suite, item, vram, tag)
                with lock, open(out, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    print(f"{cond} s{seed} {suite} {item['id']}: {'ok' if rec['correct'] else 'x '} {rec['ms']} ms "
                          f"{rec.get('tokens', {}).get('total_tokens', 0)} tok {rec.get('answer', rec.get('error', ''))[:60]!r}", flush=True)

            # workers > 1 only where every model stays loaded: each task has its own swap manager
            with ThreadPoolExecutor(workers) as pool:
                list(pool.map(lambda si: one(*si), todo))


def report(quick=False, tag=""):
    """Markdown tables from eval/results; the narrative in REPORT.md is written by hand."""
    recs = []
    for p in sorted((RESULTS / tag).glob(f"{'quick-' if quick else ''}[A-Z]*-s*.jsonl")):
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
    table("items with a forced answer % (thinking hit its cap, answered from the partial reasoning)",
          lambda rs: 100 * sum(bool(r.get("forced")) for r in rs) / len(rs))
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
    if any("level" in r for r in recs):
        out += _traces(recs, conds)
    return "\n".join(out)


def _traces(recs, conds):
    """v3 rows read from the traces: which level the answer came from, and whether reflection and search fixed more
    candidates than they broke. Judged per change on the candidate answers, not on the final answer."""
    items = {(s, it["id"]): it for s in N for it in load_suite(s)}
    out = ["\n### v3 from traces (seed 0): level the answer came from; reflection and search changes\n\n"
           "| cond | medium | high | xhigh | reflect asked / changed / fixed / broke | search steps / not first / fixed / broke |\n"
           "|---|---|---|---|---|---|"]
    for c in conds:
        lv, rf, se = {}, [0, 0, 0, 0], [0, 0, 0, 0]
        for r in (r for r in recs if r["cond"] == c and r["seed"] == 0 and "level" in r):
            lv[r["level"]] = lv.get(r["level"], 0) + 1
            p = config.ROOT / "runs" / r["task_id"] / "trace.jsonl"
            if not p.exists():
                continue
            ok = lambda a: judge(r["suite"], items[(r["suite"], r["id"])], a)[0]   # noqa: E731
            for t in jsonl(p):
                if t["kind"] == "reflect":
                    rf[0] += 1
                    if t["changed"]:
                        b, a = ok(t["before"]), ok(t["answer"])
                        rf[1] += 1; rf[2] += a and not b; rf[3] += b and not a
                elif t["kind"] == "search":
                    i = t["scores"].index(max(t["scores"]))
                    se[0] += 1
                    if i:
                        b, a = ok(t["answers"][0]), ok(t["answers"][i])
                        se[1] += 1; se[2] += a and not b; se[3] += b and not a
        out.append(f"| {c} | " + " | ".join(str(lv.get(l, 0)) for l in ("medium", "high", "xhigh")) +
                   f" | {' / '.join(map(str, rf))} | {' / '.join(map(str, se))} |")
    return out


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
