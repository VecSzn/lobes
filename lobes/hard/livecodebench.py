"""LiveCodeBench code generation, release_v6 (contests 2025-01-04 .. 2025-04-06), all 175 problems.

test6.jsonl is the increment v6 adds over v5, so the window is the file: no sampling, no cut rule.
The problems postdate every model we run, which is the point of the benchmark.

Judged the way the published numbers are: the official harness runs the answer against public +
private tests and the problem counts only if all of them pass (pass@1). The harness is vendored
under lcb_official/, unchanged, and runs in a subprocess so a runaway answer dies with it.
"""
import base64
import json
import os
import pickle
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

from lobes import config

FILE = "lcb_test6.jsonl"
URL = "https://huggingface.co/datasets/livecodebench/code_generation_lite/resolve/main/test6.jsonl"
TIMEOUT = 6                     # official per-test limit; the release carries no per-problem one
MEM_BYTES = 4 << 30             # the cap official reliability_guard documents, enforced per child

SYSTEM = ("You are an expert Python programmer. You will be given a question (problem specification) "
          "and will generate a correct Python program that matches the specification and passes all tests.")
WITH_STARTER = ("You will use the following starter code to write the solution to the problem and "
                "enclose your code within delimiters.")
WITHOUT_STARTER = ("Read the inputs from stdin solve the problem and write the answer to stdout (do not "
                   "directly test on the sample inputs). Enclose your code within delimiters as follows. "
                   "Ensure that when the python program runs, it reads the inputs, runs the algorithm and "
                   "writes output to STDOUT.")


def _prompt(row):
    """lcb_runner.prompts.code_generation.get_generic_question_template_answer, with its system message."""
    starter = row["starter_code"]
    body = WITH_STARTER if starter else WITHOUT_STARTER
    code = starter if starter else "# YOUR CODE HERE"
    return (f"{SYSTEM}\n\n### Question:\n{row['question_content']}\n\n### Format: {body}\n"
            f"```python\n{code}\n```\n\n### Answer: (use the provided format with backticks)\n\n")


def load(data_dir):
    """All 175 problems, ordered by contest date then question id. The test cases stay on disk: they
    decompress to 282 MB, and only the problems a model actually answered are ever needed."""
    path = Path(data_dir) / FILE
    items, offset = [], 0
    with path.open("rb") as f:
        for raw in f:
            row = json.loads(raw)
            items.append({"id": f"lcb-{row['question_id']}", "prompt": _prompt(row),
                          "fn_name": json.loads(row["metadata"]).get("func_name"),
                          "date": row["contest_date"], "platform": row["platform"],
                          "difficulty": row["difficulty"], "src": str(path),
                          "off": offset, "len": len(raw)})
            offset += len(raw)
    items.sort(key=lambda it: (it["date"], it["id"]))
    assert len({it["id"] for it in items}) == len(items), "duplicate question_id"
    return items


def _tests(item):
    """Public then private cases, the order the official get_evaluation_sample uses."""
    with open(item["src"], "rb") as f:
        f.seek(item["off"])
        row = json.loads(f.read(item["len"]))
    cases = json.loads(row["public_test_cases"])
    try:
        cases += json.loads(row["private_test_cases"])
    except ValueError:      # the release ships them base64'd over zlib over pickle
        cases += json.loads(pickle.loads(zlib.decompress(base64.b64decode(row["private_test_cases"]))))
    return cases


def _extract(answer):
    """Official extract_code: the last fenced block. Unfenced it returns "", which would score every
    plain-code answer zero, so an answer with no fence is taken as code the way the other code suites do."""
    lines = (answer or "").split("\n")
    fences = [i for i, l in enumerate(lines) if "```" in l]
    if len(fences) >= 2:
        return "\n".join(lines[fences[-2] + 1:fences[-1]])
    return answer or ""


def judge(item, answer):
    """-> (correct, abstained). pass@1: every public and private test has to pass. Never abstains."""
    code = _extract(answer)
    if not code.strip():
        return False, False
    cases = _tests(item)
    payload = {"code": code, "timeout": TIMEOUT,
               "input_output": json.dumps({"inputs": [c["input"] for c in cases],
                                           "outputs": [c["output"] for c in cases],
                                           "fn_name": item.get("fn_name")})}
    # official check_correctness' backstop, kept: the per-test alarm can be missed inside a C call
    outer = (TIMEOUT + 1) * len(cases) + 5
    with tempfile.TemporaryDirectory() as d:
        pay, res = Path(d) / "in.json", Path(d) / "out.json"
        pay.write_text(json.dumps(payload), encoding="utf-8")
        try:
            subprocess.run([sys.executable, "-m", "lobes.hard.livecodebench", "--child", str(pay), str(res)],
                           cwd=str(config.ROOT), capture_output=True, timeout=outer,
                           env=dict(os.environ, PYTHONIOENCODING="utf-8"))
        except subprocess.TimeoutExpired:
            return False, False
        if not res.exists():                    # crashed, killed by the OOM killer, or hard-exited
            return False, False
        return bool(json.loads(res.read_text(encoding="utf-8"))["passed"]), False


def _shim_alarm():
    """Windows has no SIGALRM. Rebuild signal.alarm on a watchdog thread that throws the harness'
    own TimeoutException into the main thread, so grade_stdio/grade_call_based read it as a TLE."""
    import signal
    if hasattr(signal, "SIGALRM"):
        return
    import ctypes
    import threading

    from .lcb_official.testing_util import TimeoutException
    main_id = ctypes.c_ulong(threading.main_thread().ident)
    pending = []

    def alarm(seconds):
        while pending:
            pending.pop().cancel()
        if seconds:
            # only lands between bytecodes: a wait inside a C call runs on until the outer timeout
            t = threading.Timer(seconds, lambda: ctypes.pythonapi.PyThreadState_SetAsyncExc(
                main_id, ctypes.py_object(TimeoutException)))
            t.daemon = True
            pending.append(t)
            t.start()

    real = signal.signal
    signal.SIGALRM = 14
    signal.alarm = alarm
    signal.signal = lambda s, h, _r=real: None if s == 14 else _r(s, h)


def _cap_memory():
    """MEM_BYTES per answer, so an allocation bomb takes its own process down and nothing else."""
    try:
        import resource
    except ImportError:
        return _cap_memory_windows()
    for which in (resource.RLIMIT_AS, resource.RLIMIT_DATA):
        _, hard = resource.getrlimit(which)
        resource.setrlimit(which, (MEM_BYTES if hard < 0 else min(MEM_BYTES, hard), hard))


def _cap_memory_windows():
    """No rlimits here, so poll our own private commit and exit when it passes the cap.
    ponytail: 50 ms between samples, a fast allocator overshoots by roughly a GB before the exit."""
    import ctypes
    import threading
    import time
    from ctypes import wintypes

    counters = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)]
    counters += [(n, ctypes.c_size_t) for n in
                 ("PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage",
                  "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]
    pmc_type = type("PROCESS_MEMORY_COUNTERS", (ctypes.Structure,), {"_fields_": counters})
    pmc = pmc_type()
    pmc.cb = ctypes.sizeof(pmc)
    get_info = ctypes.WinDLL("psapi").GetProcessMemoryInfo
    # without argtypes the -1 pseudo handle is passed as a 32-bit int and every call fails
    get_info.argtypes = [wintypes.HANDLE, ctypes.POINTER(pmc_type), wintypes.DWORD]
    get_info.restype = wintypes.BOOL
    me = wintypes.HANDLE(-1)

    def watch():
        while True:
            if get_info(me, ctypes.byref(pmc), pmc.cb) and pmc.PagefileUsage > MEM_BYTES:
                os._exit(1)
            time.sleep(0.05)

    threading.Thread(target=watch, daemon=True).start()


def _child(payload_path, result_path):
    """One problem in its own process: grading itself is the vendored official run_test."""
    from .lcb_official.testing_util import run_test
    payload = json.loads(Path(payload_path).read_text(encoding="utf-8"))
    _shim_alarm()
    _cap_memory()
    try:
        results, _ = run_test({"input_output": payload["input_output"]}, test=payload["code"],
                              debug=False, timeout=payload["timeout"])
        # official compute_metrics_from_results: passed iff every entry is > 0 (True, or an error code)
        passed = bool(results) and all(r > 0 for r in results)
    except BaseException:
        passed = False
    Path(result_path).write_text(json.dumps({"passed": passed}), encoding="utf-8")


# Hand-written reference solutions for the self-check: the release ships no solutions, so the only way
# to show the two graders accept correct code is to write some. Five of each format.
GOLD = {
    "lcb-abc387_a": "a, b = map(int, input().split())\nprint((a + b) ** 2)",
    "lcb-abc387_b": "x = int(input())\nprint(sum(i * j for i in range(1, 10) for j in range(1, 10) if i * j != x))",
    "lcb-abc388_a": "s = input()\nprint(s[0] + 'UPC')",
    "lcb-abc388_b": ("n, d = map(int, input().split())\n"
                     "snakes = [tuple(map(int, input().split())) for _ in range(n)]\n"
                     "for k in range(1, d + 1):\n"
                     "    print(max(t * (l + k) for t, l in snakes))"),
    "lcb-abc389_a": "s = input()\nprint(int(s[0]) * int(s[2]))",
    "lcb-3747": ("class Solution:\n    def maxAdjacentDistance(self, nums: List[int]) -> int:\n"
                 "        return max(abs(nums[i] - nums[i - 1]) for i in range(len(nums)))"),
    "lcb-3731": ("class Solution:\n    def subarraySum(self, nums: List[int]) -> int:\n"
                 "        return sum(sum(nums[max(0, i - nums[i]):i + 1]) for i in range(len(nums)))"),
    "lcb-3704": ("class Solution:\n    def countPartitions(self, nums: List[int]) -> int:\n"
                 "        return len(nums) - 1 if sum(nums) % 2 == 0 else 0"),
    "lcb-3736": ("class Solution:\n    def findValidPair(self, s: str) -> str:\n"
                 "        c = Counter(s)\n"
                 "        for a, b in zip(s, s[1:]):\n"
                 "            if a != b and c[a] == int(a) and c[b] == int(b):\n"
                 "                return a + b\n"
                 "        return ''"),
    "lcb-3753": ("class Solution:\n    def maxDifference(self, s: str) -> int:\n"
                 "        f = Counter(s).values()\n"
                 "        return max(v for v in f if v % 2) - min(v for v in f if v % 2 == 0)"),
}
LOOP_STDIN = "while True:\n    pass"
LOOP_CALL = ("class Solution:\n    def maxAdjacentDistance(self, nums: List[int]) -> int:\n"
             "        while True:\n            pass")
BOMB = "a = []\nwhile True:\n    a.append(bytearray(1 << 24))"


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--child":
        _child(sys.argv[2], sys.argv[3])
        sys.exit(0)

    import time

    items = load(config.ROOT / "eval" / "data")
    by_id = {it["id"]: it for it in items}
    call = [it for it in items if it["fn_name"]]
    print(f"loaded {len(items)} problems: {len(items) - len(call)} stdin, {len(call)} functional")
    print(f"window {items[0]['date'][:10]} .. {items[-1]['date'][:10]}, ids {items[0]['id']}..{items[-1]['id']}")

    t0 = time.perf_counter()
    bad = []
    for qid, sol in GOLD.items():
        it = by_id[qid]
        t1 = time.perf_counter()
        ok = judge(it, "```python\n%s\n```" % sol)[0]
        kind = "functional" if it["fn_name"] else "stdin"
        print(f"   {qid:<16} {kind:<10} {len(_tests(it)):>3} tests  {time.perf_counter() - t1:5.1f}s  "
              f"{'pass' if ok else 'FAIL'}")
        if not ok:
            bad.append(qid)
    print(f"1. hand-written solutions: {len(GOLD) - len(bad)}/{len(GOLD)} judged correct "
          f"({time.perf_counter() - t0:.1f}s)" + (", failed: " + " ".join(bad) if bad else ""))

    wrong = {"lcb-abc387_a": "a, b = map(int, input().split())\nprint(a + b)",
             "lcb-abc388_a": "s = input()\nprint(s[0] + 'UPD')",
             "lcb-3747": ("class Solution:\n    def maxAdjacentDistance(self, nums: List[int]) -> int:\n"
                          "        return max(abs(nums[i] - nums[i - 1]) for i in range(1, len(nums)))"),
             "lcb-3704": ("class Solution:\n    def countPartitions(self, nums: List[int]) -> int:\n"
                          "        return len(nums)")}
    t0 = time.perf_counter()
    for qid, sol in wrong.items():
        assert not judge(by_id[qid], "```python\n%s\n```" % sol)[0], qid
    print(f"2. {len(wrong)} broken solutions: all judged wrong ({time.perf_counter() - t0:.1f}s)")

    for empty in ("", None, "   ", "Sorry, I can't solve this."):
        assert not judge(by_id["lcb-abc387_a"], empty)[0], repr(empty)
    print("3. empty and prose answers: judged wrong")

    for qid, sol in (("lcb-abc387_a", LOOP_STDIN), ("lcb-3747", LOOP_CALL)):
        t1 = time.perf_counter()
        ok = judge(by_id[qid], "```python\n%s\n```" % sol)[0]
        kind = "functional" if by_id[qid]["fn_name"] else "stdin"
        assert not ok, qid
        print(f"4. `while True: pass` ({kind}): judged wrong in {time.perf_counter() - t1:.1f}s")

    t1 = time.perf_counter()
    assert not judge(by_id["lcb-abc387_a"], "```python\n%s\n```" % BOMB)[0]
    print(f"5. allocation bomb: judged wrong in {time.perf_counter() - t1:.1f}s "
          f"(cap {MEM_BYTES >> 30} GiB)")

    print("livecodebench ok")
