"""VRAM-budget LRU on top of llama-server's router mode.

The router only counts models (--models-max). We count megabytes: before loading X we unload
the least recently used non-resident model until X fits. Every load/unload is timed and the
real nvidia-smi reading is kept next to our estimate so the yaml numbers can be corrected.
"""
import subprocess
import threading
import time

import httpx

_lock = threading.Lock()


class ModelManager:
    def __init__(self, cfg):
        self.models = cfg["models"]
        self.base = f"http://{cfg['llama']['host']}:{cfg['llama']['port']}"
        self.budget = cfg["llama"]["vram_budget_mb"]
        self.last_used = {}  # name -> use counter; not time.time(), which ticks every 15 ms on Windows
        self.uses = 0
        self.events = []  # (name, "load"|"unload", ms, vram_after_mb)

    def _touch(self, name):
        self.uses += 1
        self.last_used[name] = self.uses

    # --- router api ---
    def alive(self):
        try:
            return httpx.get(self.base + "/health", timeout=3).status_code == 200
        except httpx.HTTPError:
            return False

    def status(self):
        r = httpx.get(self.base + "/models", timeout=10)
        r.raise_for_status()
        return {m["id"]: m["status"]["value"] for m in r.json()["data"]}

    def loaded(self):
        return [n for n, s in self.status().items() if s == "loaded"]

    def used_mb(self):
        return sum(self.models.get(n, {}).get("vram_mb", 0) for n in self.loaded())

    def ensure(self, name):
        if name not in self.models:
            raise KeyError(f"unknown model {name}, add it to lobes.yaml")
        with _lock:     # tasks running in parallel share the router; a second load of a loading model is a 400
            return self._ensure(name)

    def _ensure(self, name):
        st = self.status().get(name)
        if st == "loaded":
            self._touch(name)
            return 0
        self._free(self.models[name]["vram_mb"])
        t0 = time.perf_counter()
        if st != "loading":
            httpx.post(self.base + "/models/load", json={"model": name}, timeout=30).raise_for_status()
        self._wait(name, "loaded")
        ms = int((time.perf_counter() - t0) * 1000)
        self._touch(name)
        self.events.append((name, "load", ms, self.vram_now_mb()))
        return ms

    def unload(self, name):
        t0 = time.perf_counter()
        httpx.post(self.base + "/models/unload", json={"model": name}, timeout=30).raise_for_status()
        self._wait(name, "unloaded")
        ms = int((time.perf_counter() - t0) * 1000)
        self.events.append((name, "unload", ms, self.vram_now_mb()))
        return ms

    def _free(self, need):
        while self.used_mb() + need > self.budget:
            victims = [n for n in self.loaded()
                       if n in self.models and not self.models[n].get("resident") and self.models[n]["vram_mb"] > 0]
            if not victims:
                raise RuntimeError(f"cannot fit {need} MB: loaded={self.loaded()} budget={self.budget}")
            self.unload(min(victims, key=lambda n: self.last_used.get(n, 0)))

    def _wait(self, name, want, timeout=300):
        t0 = time.time()
        while time.time() - t0 < timeout:
            r = httpx.get(self.base + "/models", timeout=10).json()["data"]
            st = next((m["status"] for m in r if m["id"] == name), None)
            if st is None:
                raise RuntimeError(f"router does not know {name}; run `lobes install` to regenerate models.ini")
            if st["value"] == want:
                return
            if st.get("failed"):
                raise RuntimeError(f"{name} failed to load, exit {st.get('exit_code')}; see llama-server log")
            time.sleep(0.3)
        raise TimeoutError(f"{name} did not reach {want} in {timeout}s")

    # --- gpu ---
    @staticmethod
    def vram_now_mb():
        try:
            out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                                 capture_output=True, text=True, timeout=5).stdout
            return int(out.strip().splitlines()[0])
        except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
            return None
