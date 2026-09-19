"""lobes eval: the suites, judges and conditions the comparison runs on. One JSONL line per item, resumable."""
import json
import os
import random
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

from . import config, providers
from .models import ModelManager
from .runner import run

# The judges below were fixed before the runs; the runtime never grades itself.
NUM = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def nums(s):
    return [n.replace(",", "") for n in NUM.findall(s or "")]


def norm(s):
    s = re.sub(r"[^\w\s]", " ", (s or "").lower())
    return " ".join(w for w in s.split() if w not in ("the", "a", "an", "is", "are", "was", "of"))


def same(a, b):
    na, nb = nums(a), nums(b)
    if na and nb:
        x, y = float(na[-1]), float(nb[-1])
        return abs(x - y) <= 1e-6 * max(1.0, abs(y))
    a, b = norm(a) or (a or "").strip().lower(), norm(b) or (b or "").strip().lower()
    if not a or not b:
        return False
    ta, tb = set(a.split()), set(b.split())
    if ta <= tb or tb <= ta:
        return True
    return len(ta & tb) / len(ta | tb) >= 0.5


DATA = config.ROOT / "eval" / "data"
RESULTS = config.ROOT / "eval" / "results"
SHUFFLE_SEED = 20260914
# v1/v2 ran gsm8k 30, tools 20, ocrbench 20, multistep 10, the cut rule of that round; v3 and v4 enlarged them.
# the first items of an enlarged suite are the ones that ran before, the shuffle seed did not change.
# the last 30 of each (tools-50 up, multi-40 up) are the harder v4 halves, reported apart from the older ones.
N = {"gsm8k": 200, "humaneval": 30, "tools": 60, "simpleqa": 30, "ocrbench": 50, "multistep": 60, "aime": 30,
     # the suites above saturate: multistep 95%, humaneval 90%, so a paired test has almost nothing to work with.
     # these are the sets other models report. gpqa and humanevalplus run whole, which is how they are published.
     "gpqa": 198, "humanevalplus": 164, "mmlupro": 200, "mbppplus": 200, "ifeval": 541, "math500": 500,
     "bfcl": 500, "lcb": 175, "repo": 80}
# a run with no --suites keeps the old ruler; the sets added below the line in N are asked for by name
DEFAULT = ("gsm8k", "humaneval", "tools", "simpleqa", "ocrbench", "multistep", "aime")
CODE = ("humaneval", "humanevalplus", "mbppplus")       # judged by running the answer, so the fence comes off first
SMALL = 0                     # seeds 1 and 2: first SMALL items of every suite except multistep


def jsonl(path):
    # not splitlines(): a U+2028 inside a model's answer counts as a line break there and cuts the record in two
    return [json.loads(l) for l in path.read_text(encoding="utf-8").split("\n") if l.strip()]


CONDITIONS = {                # cfg overrides on top of the profile, one per arm of the comparison
    "R":  dict(profile="single-9b", raw=True),      # the 9B as shipped: one chat call, no lobes, no tools
    "T":  dict(profile="bare-9b"),                  # the 9B thinking at the same level with every tool, no other lobe
    "A":  dict(profile="single-9b"),
    "B":  dict(profile="single-4b"),
    "B3": dict(profile="single-4b", vote=3),
    "C":  dict(profile="shared"),
    "D":  dict(profile="specialists"),
    "V1": dict(profile="v1"),
}
VISION = {"B", "B3", "C", "D"}


def spec(cond):
    """A condition above, or any profile name run as that profile."""
    return CONDITIONS.get(cond) or dict(profile=cond)
ABSTAIN = re.compile(r"don'?t know|do not know|not sure|cannot (find|determine|verify)|no (reliable )?information"
                     r"|unknown|unable to", re.I)
FILES = {
    "gsm8k_test.parquet": "https://huggingface.co/datasets/openai/gsm8k/resolve/main/main/test-00000-of-00001.parquet",
    "humaneval_test.parquet": "https://huggingface.co/datasets/openai/openai_humaneval/resolve/main/openai_humaneval/test-00000-of-00001.parquet",
    "simpleqa_test.csv": "https://openaipublic.blob.core.windows.net/simple-evals/simple_qa_test_set.csv",
    "ocrbench_test.parquet": "https://huggingface.co/datasets/echo840/OCRBench/resolve/main/data/test-00000-of-00001.parquet",
    "aime_test.parquet": "https://huggingface.co/datasets/yentinglin/aime_2025/resolve/main/data/train-00000-of-00001-243207c6c994e1bd.parquet",
    # Idavidrein/gpqa is gated; this mirror is the diamond split already written as four choices with a
    # \boxed{letter} instruction, which is the form the published numbers are scored in.
    "gpqa_test.parquet": "https://huggingface.co/datasets/hendrydong/gpqa_diamond_mc/resolve/main/data/test-00000-of-00001.parquet",
    "mmlupro_test.parquet": "https://huggingface.co/datasets/TIGER-Lab/MMLU-Pro/resolve/main/data/test-00000-of-00001.parquet",
    "humanevalplus_test.parquet": "https://huggingface.co/datasets/evalplus/humanevalplus/resolve/main/data/test-00000-of-00001-5973903632b82d40.parquet",
    "mbppplus_test.parquet": "https://huggingface.co/datasets/evalplus/mbppplus/resolve/main/data/test-00000-of-00001-d5781c9c51e02795.parquet",
    "ifeval_test.jsonl": "https://huggingface.co/datasets/google/IFEval/resolve/main/ifeval_input_data.jsonl",
    "math500_test.jsonl": "https://huggingface.co/datasets/HuggingFaceH4/MATH-500/resolve/main/test.jsonl",
    # BFCL ships the questions and the accepted answers as separate files, and the two must be the same
    # version: main is v4 now and three v3 questions were reworded, so a v3/v4 mix would score them wrong.
    **{f"bfcl_{s}{k}.json": "https://raw.githubusercontent.com/ShishirPatil/gorilla/main/"
       f"berkeley-function-call-leaderboard/bfcl_eval/data/{'possible_answer/' if k else ''}BFCL_v4_{s}.json"
       for s in ("live_simple", "live_multiple", "live_parallel", "live_parallel_multiple")
       for k in ("", "_answer")},
    # 128 MiB, and it stays whole: the tests are zlib blobs read back by byte offset when an item is judged
    "lcb_test6.jsonl": "https://huggingface.co/datasets/livecodebench/code_generation_lite/resolve/main/test6.jsonl",
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


def _letter(text, n=4):
    """A committed choice, or "" when the model never committed. n is how many options that item has.

    Loose extraction is worth roughly n_options^-1 of free score, because a chemistry answer is full of C3
    and a genetics one of G2, and a reply that ran out of tokens mid-sentence has not answered at all. So
    only a box, an explicit "answer is X", or a line holding nothing but the letter counts.
    """
    hi = chr(ord("A") + n - 1)
    text = (text or "").strip()
    m = re.findall(rf"\\boxed\{{\s*\(?([A-{hi}])\)?[.)]?\s*\}}", text)
    if m:
        return m[-1]
    m = re.findall(rf"(?:final answer|answer|option|choice)\s*(?:is|:|=)\s*\**\(?([A-{hi}])\)?\**\s*(?:[.,;:)\]]|$)",
                   text, re.I | re.M)
    if m:
        return m[-1]
    m = re.fullmatch(rf"\W*\(?([A-{hi}])\)?[.)]?\W*", text.splitlines()[-1] if text else "")
    return m.group(1) if m else ""


def _run_code(src, seconds):
    # via a file, not -c: the plus suites inline their whole input list and blow past the Windows command line limit
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "item.py"
        p.write_text(src, encoding="utf-8")
        try:
            return subprocess.run([sys.executable, str(p)], capture_output=True, timeout=seconds).returncode == 0, False
        except subprocess.TimeoutExpired:
            return False, False


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
    elif name == "gpqa":                        # GPQA diamond, whole; the prompt already carries its four choices
        df = pd.read_parquet(DATA / "gpqa_test.parquet")
        items = [{"id": f"gpqa-{i}", "prompt": p, "gold": _letter(s), "n": 4}
                 for i, (p, s) in enumerate(zip(df.problem, df.solution))]
    elif name == "mmlupro":
        df = pd.read_parquet(DATA / "mmlupro_test.parquet")
        items = [{"id": f"mmlupro-{i}", "gold": r.answer, "n": len(r.options),
                  "prompt": r.question + "\n\n" + "\n".join(f"({chr(65 + k)}) {o}" for k, o in enumerate(r.options))
                  + "\n\nPlease write your final answer as \\boxed{letter}."}
                 for i, r in enumerate(df.itertuples())]
    elif name == "humanevalplus":               # the same 164 problems as humaneval, with the tests that catch more
        df = pd.read_parquet(DATA / "humanevalplus_test.parquet")
        items = [{"id": r.task_id.replace("HumanEval/", "HumanEvalPlus-"),
                  "prompt": "Complete this python function. Reply with the complete "
                  "function, signature and imports included, no explanation.\n\n" + r.prompt,
                  "source": r.prompt, "test": r.test, "entry_point": r.entry_point} for r in df.itertuples()]
    elif name == "mbppplus":
        # its test calls the function by name at module level instead of defining check(), and the name is only
        # pinned by the example assert, which is why that assert is part of the prompt the way MBPP ships it.
        df = pd.read_parquet(DATA / "mbppplus_test.parquet")
        items = [{"id": f"mbppplus-{r.task_id}", "test": "\n".join(list(r.test_imports)) + "\n" + r.test,
                  "prompt": "Write a python function for this. Reply with the complete function, imports "
                  "included, no explanation. It must have exactly the name and signature this example uses.\n\n"
                  + r.prompt + "\n\n" + (list(r.test_list)[0] if len(r.test_list) else "")}
                 for r in df.itertuples()]
    elif name == "ifeval":                      # checkers are the official ones, vendored; needs nltk punkt_tab
        from .hard.ifeval import load as _load
        items = _load(DATA)
    elif name == "math500":                     # the \boxed{} judged for math equality, not string equality
        from .hard.math500 import load as _load
        items = _load(DATA)
    elif name == "bfcl":                        # already cut to 500 by a fixed rule, so it skips _pick
        from .hard.bfcl import load as _load
        return _load(DATA)
    elif name == "lcb":                         # LiveCodeBench release_v6, 2501-2504, whole; also skips _pick
        from .hard.livecodebench import load as _load
        return _load(DATA)
    elif name == "repo":                        # generated from a package on disk, so it picks its own spread
        from .hard.repo import load as _load
        return _load(DATA)
    elif name == "aime":                        # AIME 2025, both papers; every answer is an integer 0 to 999
        df = pd.read_parquet(DATA / "aime_test.parquet")
        items = [{"id": f"aime-{i}", "prompt": p, "gold": str(a)} for i, (p, a) in enumerate(zip(df.problem, df.answer))]
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
    """-> (correct, abstained). The judges do not change between runs."""
    answer = answer or ""
    if suite in ("gsm8k", "aime"):
        return same(answer, item["gold"]), False
    if suite in ("humaneval", "humanevalplus"):
        code = re.sub(r"^\s*```\w*\n|\n```\s*$", "", answer.rstrip())   # keep the indentation of body-only answers
        src = code if f"def {item['entry_point']}" in code else item["source"] + code
        # the plus tests run hundreds of extra inputs per problem, so they need a longer leash than the originals
        return _run_code(src + "\n\n" + item["test"] + f"\ncheck({item['entry_point']})",
                         10 if suite == "humaneval" else 60)
    if suite == "mbppplus":
        # no check() to call here: the test calls the function by name at module level, so just run the two together
        code = re.sub(r"^\s*```\w*\n|\n```\s*$", "", answer.rstrip())
        return _run_code(code + "\n\n" + item["test"], 60)
    if suite in ("gpqa", "mmlupro"):            # abstained = never committed to a letter, usually cut off mid-run
        got = _letter(answer, item["n"])
        return got == item["gold"], not got
    if suite == "ifeval":                       # prompt-level strict, the figure models publish as "IFEval"
        from .hard.ifeval import judge as _j
        return _j(item, answer)
    if suite == "math500":                      # lazy import: math_verify drags in sympy, about a second
        from .hard.math500 import judge as _j
        return _j(item, answer)
    if suite == "bfcl":                         # official AST match; prose around the call fails, as on the board
        from .hard.bfcl import judge as _j
        return _j(item, answer)
    if suite == "lcb":                          # not in CODE: it takes the last fence itself, by the official rule
        from .hard.livecodebench import judge as _j
        return _j(item, answer)
    if suite == "tools":
        g = item["answer"]
        ok = same(answer, g) if re.fullmatch(r"-?[\d.]+", g) else norm(g) in norm(answer)
        return ok, False
    if suite == "repo":                         # abstaining matters here: a model that never found the file says so
        g = item["gold"]
        ok = same(answer, g) if re.fullmatch(r"-?[\d.]+", g) else norm(g) in norm(answer)
        return ok, bool(ABSTAIN.search(answer))
    if suite == "simpleqa":
        return norm(item["gold"]) in norm(answer), bool(ABSTAIN.search(answer))
    if suite == "ocrbench":
        return any(norm(g) in norm(answer) for g in item["gold"]), False
    if suite == "multistep":   # a number counts wherever it is in the answer: the 9B writes "1,234"
        return all(any(same(n, x) for n in nums(answer)) if re.fullmatch(r"-?[\d.]+", x) else norm(x) in norm(answer)
                   for x in item["answers"]), False
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
    c = dict(cfg, seed=seed, **{k: v for k, v in spec(cond).items() if k != "profile"})
    task_id = f"eval-{tag + '-' if tag else ''}{cond}-s{seed}-{item['id']}"   # tagged runs keep their own traces
    shutil.rmtree(cfg["_root"] / "runs" / task_id, ignore_errors=True)
    vram.peak = 0
    t0 = time.perf_counter()
    rec = {"cond": cond, "seed": seed, "suite": suite, "id": item["id"], "task_id": task_id, "effort": c.get("effort") or "medium"}
    try:
        if c.get("raw"):
            return raw_item(c, cond, suite, item, vram, rec)
        st = run(c, item["prompt"], profile=spec(cond)["profile"], images=item.get("images"), task_id=task_id)
    except Exception as e:                      # one broken item must not kill the night
        rec.update(error=repr(e)[:500], ms=int((time.perf_counter() - t0) * 1000), correct=False, abstained=False)
        return rec
    answer = code_block(st.answer) if suite in CODE else st.answer
    correct, abstained = judge(suite, item, answer)
    lobe_ms = {}
    for lobe, _, ms, _ in st.calls:
        lobe_ms[lobe] = lobe_ms.get(lobe, 0) + ms
    swap_ms = 0
    for r in jsonl(cfg["_root"] / "runs" / task_id / "trace.jsonl"):
        if r["kind"] == "model":
            swap_ms += r["ms"]
    rec.update(answer=(answer or "")[:1000], correct=correct, abstained=abstained, route=st.route,
               tokens=st.usage, ms=st.ms(), lobe_ms=lobe_ms, swaps=st.swaps, swap_ms=swap_ms, vram_peak_mb=vram.peak,
               calls=len(st.calls), tools=len(st.tool_results), sent_back=len(st.problems), level=st.effort,
               stuck=bool(st.capped), capped=st.capped)
    if suite in ("gsm8k", "tools", "aime"):     # lenient twin of the strict judge, reported next to it
        rec["gold_in_answer"] = item.get("gold", item.get("answer")).replace(",", "") in nums(st.answer or "")
    return rec


RAW_TAIL = "\n\nEnd your reply with the final answer alone on the last line."


def code_block(answer):
    """The judge runs a humaneval answer as code: the last fenced block, or the whole answer without one."""
    blocks = re.findall(r"```\w*\n(.*?)```", answer or "", re.S)
    return blocks[-1] if blocks else answer


def raw_item(cfg, cond, suite, item, vram, rec):
    """The model on its own: one chat call at the runner's cold-sample temperature and seed, thinking left at the
    template default, no tools, no images. The judges read free text, so the prompt asks for the answer last."""
    t0 = time.perf_counter()
    prov, model = config.lobe(cfg, "reasoning", spec(cond)["profile"])
    mm = ModelManager(cfg)
    mm.ensure(model)
    r = providers.chat(cfg["providers"][prov], model, [{"role": "user", "content": item["prompt"] + RAW_TAIL}],
                       temperature=0.2, max_tokens=12000, seed=cfg.get("seed"))
    answer = r.text.strip()
    if suite in CODE:
        answer = code_block(answer)
    correct, abstained = judge(suite, item, answer)
    rec.update(answer=answer[:1000], correct=correct, abstained=abstained, route="raw", tokens=r.usage,
               ms=int((time.perf_counter() - t0) * 1000), lobe_ms={"raw": r.ms},
               swaps=sum(op == "load" for _, op, _, _ in mm.events), swap_ms=sum(ms for _, op, ms, _ in mm.events),
               vram_peak_mb=vram.peak, calls=1, tools=0, sent_back=0,
               stuck=False, finish=r.finish)
    if suite in ("gsm8k", "tools", "aime"):
        rec["gold_in_answer"] = item.get("gold", item.get("answer")).replace(",", "") in nums(answer)
    return rec


def plan(cond, seed, quick, suites=None, ids=None):
    for suite in suites or DEFAULT:
        if suite == "ocrbench" and cond not in VISION and not suites:   # named suites run as asked: profiles have vision too
            continue
        n = 3 if quick else N[suite] if seed == 0 or suite == "multistep" else SMALL
        items = load_suite(suite)
        yield suite, [it for it in items if it["id"] in ids] if ids else items[:n]


def main(cfg, conditions, seeds, quick=False, suites=None, tag="", workers=1, ids=None):
    sys.stdout.reconfigure(errors="replace")  # windows console is gbk; an umlaut in an answer killed a run
    fetch()
    unknown = ids - {it["id"] for s in suites or N for it in load_suite(s)} if ids else set()
    if unknown:
        raise SystemExit(f"unknown ids: {', '.join(sorted(unknown))}")
    results = RESULTS / tag                   # a tag keeps one code version's run apart from another's
    results.mkdir(parents=True, exist_ok=True)
    vram = Vram()
    vram.start()
    for cond in conditions:
        for seed in seeds:
            out = results / f"{'quick-' if quick else ''}{cond}-s{seed}.jsonl"
            # two runs into one tag both read `done` as empty and each wrote the whole suite, so the file
            # is claimed before anything is read.
            running = out.with_suffix(".running")
            try:
                os.close(os.open(running, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            except FileExistsError:
                raise SystemExit(f"{running} is there: another run is writing {out.name}, or one died mid-run. "
                                 f"Check the file, then delete the marker to carry on.") from None
            done = set()
            if out.exists():
                done = {(r["suite"], r["id"]) for r in jsonl(out)}
            todo = [(suite, item) for suite, items in plan(cond, seed, quick, suites, ids) for item in items
                    if (suite, item["id"]) not in done]
            lock = threading.Lock()

            def one(suite, item):
                rec = run_item(cfg, cond, seed, suite, item, vram, tag)
                with lock, open(out, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    print(f"{cond} s{seed} {suite} {item['id']}: {'ok' if rec['correct'] else 'x '} {rec['ms']} ms "
                          f"{rec.get('tokens', {}).get('total_tokens', 0)} tok {rec.get('answer', rec.get('error', ''))[:60]!r}", flush=True)

            # workers > 1 only where every model stays loaded: each task has its own swap manager
            try:
                with ThreadPoolExecutor(workers) as pool:
                    list(pool.map(lambda si: one(*si), todo))
            finally:
                running.unlink(missing_ok=True)


def report(quick=False, tag=""):
    """Markdown tables from eval/results; any narrative around them is written by hand."""
    recs = []
    for p in sorted((RESULTS / tag).glob("*-s*.jsonl")):
        if p.name.startswith("quick-") != quick:    # a case-insensitive glob cannot tell these apart on windows
            continue
        recs += jsonl(p)
    recs = [r for r in recs if "error" not in r]
    seen, once = set(), []
    for r in recs:                  # an item written twice counts once: the first pass is the record
        key = (r["cond"], r["seed"], r["suite"], r["id"])
        if key not in seen:
            seen.add(key)
            once.append(r)
    if len(once) != len(recs):
        print(f"# {len(recs) - len(once)} repeated records ignored, first pass kept", file=sys.stderr)
    recs = once
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
    table("stuck % (v1/v2: step cap with no PASS; v3: any per-item cap hit)", lambda rs: 100 * sum(r["stuck"] for r in rs) / len(rs))
    table("sent back % (the review found a problem at least once)",
          lambda rs: 100 * sum(bool(r.get("sent_back")) for r in rs) / len(rs))
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
    return "\n".join(out)


if __name__ == "__main__":
    assert judge("gsm8k", {"gold": "18"}, "She makes $18")[0] and not judge("gsm8k", {"gold": "18"}, "$18 a day, 9 * 2")[0]
    assert judge("aime", {"gold": "70"}, "the sum of the bases is 070")[0] and not judge("aime", {"gold": "70"}, "b = 21")[0]
    assert judge("tools", {"answer": "Monday"}, "It is a Monday.")[0] and judge("tools", {"answer": "391"}, "391")[0]
    he = {"entry_point": "add", "source": "def add(a, b):\n", "test": "def check(c):\n    assert c(1, 2) == 3\n"}
    assert judge("humaneval", he, "```python\ndef add(a, b):\n    return a + b\n```")[0] and judge("humaneval", he, "    return a + b\n")[0]
    assert not judge("humaneval", he, "def add(a, b):\n    return a - b")[0]
    assert judge("simpleqa", {"gold": "Michio Sugeno"}, "I don't know, maybe Michio Sugeno.") == (True, True)
    assert judge("multistep", {"answers": ["210", "bob"]}, "sum is 210, best is Bob")[0]
    assert judge("multistep", {"answers": ["1234", "0642"]}, "1,234 items, ending 0642 (2029)")[0]
    assert not judge("multistep", {"answers": ["1234", "bob"]}, "1,234 items, ann")[0]
    assert not judge("ocrbench", {"gold": ["CENTRE"]}, "center")[0] and judge("ocrbench", {"gold": ["CENTRE"]}, "It says CENTRE")[0]
    mb = {"test": "assert add(1, 2) == 3\n"}
    assert judge("mbppplus", mb, "```python\ndef add(a, b):\n    return a + b\n```")[0]
    assert not judge("mbppplus", mb, "def add(a, b):\n    return a - b")[0]
    assert _letter("so \\boxed{B} it is") == "B" and _letter("The answer is (C).") == "C" and _letter("D") == "D"
    assert _letter("the answer is a number near 4") == "" and _letter("no idea") == ""
    # a reply cut off inside chemistry or genetics prose has not answered, and must not be credited
    assert _letter("bonds:\n - C3-C4 (single)\n - C5=C6") == "" and _letter("G2 is TF.\n G1 and G3 are") == ""
    assert _letter("\\boxed{E}") == "" and _letter("\\boxed{E}", 10) == "E"   # GPQA stops at D
    assert judge("gpqa", {"gold": "A", "n": 4}, "\\boxed{A}") == (True, False)
    assert judge("gpqa", {"gold": "A", "n": 4}, "...the C3 position") == (False, True)   # abstained, not wrong-guess
    assert judge("mmlupro", {"gold": "G", "n": 10}, "Answer: G")[0]
    for suite in ("tools", "multistep"):        # a mined item whose own answer fails, or whose blank fails to fail,
        items = load_suite(suite)               # is a broken item: a gold that norms to nothing matches everything
        for i, it in enumerate(items):
            gold = it["answer"] if suite == "tools" else ", ".join(it["answers"])
            assert judge(suite, it, gold)[0], it["id"]
            assert not judge(suite, it, "")[0], it["id"]
            # and it must not pass for the question next to it: these judges look for the gold anywhere in the
            # reply, so a gold that is short or common hands out points to answers written for other questions
            nxt = items[(i + 1) % len(items)]
            assert not judge(suite, nxt, gold)[0], f"{it['id']} 的答案把 {nxt['id']} 也判对了"
    print("eval judges ok")
