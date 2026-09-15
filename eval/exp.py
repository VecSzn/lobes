"""Measurement, not the pipeline: witness samples per item with the pipeline's own prompts, models and program
running, one sample per line, so any settle rule can be replayed offline (DECISIONS, 09-15 evening).
  python eval/exp.py ocr OUT.jsonl [workers]   the perception lobe on the 50 ocrbench items, six ways
  python eval/exp.py gsm OUT.jsonl [workers]   the reasoning lobe cold, plain, hot x4 and the motor lobe on gsm8k + multistep
  python eval/exp.py replay OUT.jsonl          the settle rule over those samples, plan by plan
Resumable: (id, tag, k) already in OUT are skipped."""
import collections
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from lobes import config
from lobes.eval import judge, load_suite
from lobes.lobe import Witness, agree, brief, motor, perception, reasoning, settle
from lobes.lobe.verifier import ocr_backed
from lobes.runner import Ctx, TaskState, Trace

cfg = config.load()
cfg["effort"], cfg["seed"] = "medium", None      # random seeds: hot samples must differ
out, done, lock = None, set(), threading.Lock()


def fresh(item, tag, k, task_class):
    task_id = f"exp-{tag}-{k}-{item['id']}"
    rundir = cfg["_root"] / "runs" / task_id
    ctx = Ctx(cfg, cfg["profile"], Trace(rundir / "trace.jsonl"), rundir)
    st = TaskState(task_id, item["prompt"], item.get("images") or [])
    st.task_class, st.needs_tool = task_class, task_class != "vision"
    return ctx, st


def emit(suite, item, tag, k, ctx, st, value, **extra):
    calls = [r for r in map(json.loads, open(ctx.trace.path, encoding="utf-8")) if r["kind"] == "call"]
    rec = dict(suite=suite, id=item["id"], tag=tag, k=k, value=value, ok=judge(suite, item, value or "")[0],
               forced=any(c.get("forced") for c in calls), calls=len(calls),
               completion=st.usage.get("completion_tokens", 0), total=st.usage.get("total_tokens", 0), ms=st.ms(), **extra)
    with lock:
        with open(out, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def ask(ctx, st, prompt, **kw):
    r = ctx.chat(st, "perception", [{"role": "user", "content": prompt}], schema=perception.ANSWER, images=st.images, **kw)
    return ((r.data or {}).get("answer") or r.text).strip()


def ocr_item(item):
    suite = "ocrbench"
    direct = f"Look at the image and answer with only the answer, nothing else: {item['prompt']}"
    for tag, k in (("look", 0), ("direct", 0), ("direct", 1), ("hot", 0), ("think", 0), ("afterlook", 0), ("reasoning", 0)):
        if (item["id"], tag, k) in done:
            continue
        ctx, st = fresh(item, tag, k, "vision")
        t0 = time.time()
        try:
            if tag == "look":
                d = perception.look(ctx, st)
                engine = "\n".join(r.get("stdout") or "" for r in st.tool_results.values())
                emit(suite, item, tag, k, ctx, st, d.get("text", ""), description_ok=judge(suite, item, d.get("description", ""))[0],
                     engine_ok=judge(suite, item, engine)[0], engine=engine[:300])
            elif tag == "direct":
                emit(suite, item, tag, k, ctx, st, ask(ctx, st, direct, thinking=False, max_tokens=300))
            elif tag == "hot":
                emit(suite, item, tag, k, ctx, st, ask(ctx, st, direct, thinking=False, temperature=0.7, max_tokens=300))
            elif tag == "think":
                emit(suite, item, tag, k, ctx, st, ask(ctx, st, direct, thinking=True, temperature=0.2, max_tokens=6000))
            elif tag == "afterlook":
                perception.look(ctx, st)
                emit(suite, item, tag, k, ctx, st, ask(ctx, st, brief(st) + "\n" + direct, thinking=False, max_tokens=300))
            elif tag == "reasoning":
                perception.look(ctx, st)
                w = reasoning.witness(ctx, st)
                emit(suite, item, tag, k, ctx, st, w.value, ran=w.ran, note=w.note)
        except Exception as e:
            print("fail", item["id"], tag, k, repr(e)[:200], flush=True)
        print(item["id"], tag, k, int(time.time() - t0), "s", flush=True)


STAGES = [("cold", 0, dict(thinking=True, temperature=0.2)), ("plain", 0, dict(thinking=False, temperature=0.2)),
          ("motor", 0, None), ("plainhot", 0, dict(thinking=False, temperature=0.7))] + \
         [("hot", k, dict(thinking=True, temperature=0.7)) for k in range(4)]


def gsm_item(suite, item, tag, k, kw):
    if (item["id"], tag, k) in done:
        return
    ctx, st = fresh(item, tag, k, "qa")
    t0 = time.time()
    try:
        w = motor.witness(ctx, st) if tag == "motor" else reasoning.witness(ctx, st, **kw)
        emit(suite, item, tag, k, ctx, st, w.value, ran=w.ran, note=w.note)
    except Exception as e:
        print("fail", item["id"], tag, k, repr(e)[:200], flush=True)
    print(item["id"], tag, k, int(time.time() - t0), "s", flush=True)


def replay(path):
    """The settle rule over recorded samples, witnesses in each plan's order, stopping at the first settled value;
    nothing settles and the first witness's value goes out (hedged). Tokens are the samples drawn."""
    by = collections.defaultdict(dict)
    for r in map(json.loads, open(path, encoding="utf-8")):
        by[r["id"]][(r["tag"], r["k"])] = r
    vision = any(("direct", 0) in it for it in by.values())
    items = {it["id"]: it for s in (("ocrbench",) if vision else ("gsm8k", "multistep")) for it in load_suite(s)}

    def run(it, plan):
        st, ws, used = TaskState("replay", "", []), [], []
        if ("look", 0) in it:
            st.tool_results["ocr_0"] = {"stdout": it[("look", 0)].get("engine") or "", "exit": 0}
        for key in plan:
            r = it.get(key) or (it.get(("direct", 0)) if key == ("think", 0) else None)   # a refused think call answered plain
            if not r or not r["value"]:
                continue
            used.append(r)
            ws.append(Witness("reasoning" if key[0] == "reasoning" else "perception" if vision else key[0], r["value"], ran=bool(r.get("ran"))))
            if vision and (ref := ocr_backed(st, r["value"])):
                ws.append(Witness("ocr", r["value"], ran=True, ref=ref))
            if hit := settle(ws, 2, "perception" if vision else None, items[r["id"]]["prompt"]):
                return hit[0].value, used
        return (used[0]["value"] if used else None), used

    def majority(it, keys):
        rs = [it[k] for k in keys if k in it and it[k]["value"]]
        groups = []
        for r in rs:
            for g in groups:
                if agree(g[0]["value"], r["value"], items[r["id"]]["prompt"]):
                    g.append(r)
                    break
            else:
                groups.append([r])
        return (max(groups, key=len)[0]["value"] if groups else None), rs

    C, P, M, H = ("cold", 0), ("plain", 0), ("motor", 0), [("hot", k) for k in range(4)]
    D, T, R = ("direct", 0), ("think", 0), ("reasoning", 0)
    plans = [("direct alone", [D]), ("think alone", [T]), ("direct, reasoning (before 09-15)", [D, R]),
             ("think, reasoning (shipped)", [T, R]), ("think, reasoning, direct", [T, R, D]), ("think, direct", [T, D]),
             ("direct, think", [D, T]), ("think, hot", [T, ("hot", 0)])] if vision else \
            [("cold alone", [C]), ("plain alone", [P]), ("motor alone", [M]), ("cold, motor, hot 0-1 (shipped, n=3)", [C, M] + H[:2]),
             ("cold, motor, hot 0-3 (n=5)", [C, M] + H), ("no motor: cold, hot 0-1", [C] + H[:2]), ("plain, motor, hot 0-1", [P, M] + H[:2]),
             ("majority of cold, hot 0-3", None)]
    for suite in sorted({r["suite"] for it in by.values() for r in it.values()}):
        its = {i: it for i, it in by.items() if next(iter(it.values()))["suite"] == suite}
        have = collections.Counter(k for it in its.values() for k in it)
        print(f"== {suite}: {len(its)} items; samples per tag:", dict(sorted(have.items())))
        for name, plan in plans:
            right = n = tokens = 0
            for iid, it in its.items():
                value, used = majority(it, [C] + H) if plan is None else run(it, plan)
                if value is None:
                    continue
                n += 1
                right += judge(suite, items[iid], value)[0]
                tokens += sum(u["completion"] for u in used)
            if n:
                print(f"   {name:40s} {right}/{n}   {tokens // n} completion tokens per item")
        c = collections.Counter()
        for it in its.values():
            for (tag, k), r in it.items():
                c[f"{tag} {'forced' if r.get('forced') else 'whole'}"] += 1
                c[f"{tag} {'forced' if r.get('forced') else 'whole'} right"] += r["ok"]
        print("   forced / whole thinking, and right:", dict(sorted(c.items())))


if __name__ == "__main__":
    if sys.argv[1] == "replay":
        replay(sys.argv[2])
        sys.exit()
    out = Path(sys.argv[2])
    workers = int(sys.argv[3]) if len(sys.argv) > 3 else 4
    done = {(r["id"], r["tag"], r["k"]) for r in map(json.loads, open(out, encoding="utf-8"))} if out.exists() else set()
    if sys.argv[1] == "ocr":
        with ThreadPoolExecutor(workers) as ex:
            list(ex.map(ocr_item, load_suite("ocrbench")))
    else:
        items = [("gsm8k", it) for it in load_suite("gsm8k")] + [("multistep", it) for it in load_suite("multistep")]
        for tag, k, kw in STAGES:                     # stage by stage: a partial file is still a complete stage
            print("== stage", tag, k, flush=True)
            with ThreadPoolExecutor(workers) as ex:
                list(ex.map(lambda si: gsm_item(si[0], si[1], tag, k, kw), items))
    print("done", flush=True)
