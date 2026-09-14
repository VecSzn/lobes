"""Download llama.cpp and the GGUFs a profile needs, then write the router preset file."""
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import httpx
from rich.progress import BarColumn, DownloadColumn, Progress, TextColumn, TransferSpeedColumn


def download(url, dest: Path):
    if dest.exists():
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    have = part.stat().st_size if part.exists() else 0
    headers = {"Range": f"bytes={have}-"} if have else {}
    with httpx.stream("GET", url, headers=headers, follow_redirects=True, timeout=60) as r:
        if r.status_code == 416:            # .part is already the whole file
            part.rename(dest)
            return
        r.raise_for_status()
        resumed = r.status_code == 206
        total = int(r.headers.get("content-length", 0)) + (have if resumed else 0)
        cols = [TextColumn("{task.description}"), BarColumn(), DownloadColumn(), TransferSpeedColumn()]
        with open(part, "ab" if resumed else "wb") as f, Progress(*cols) as bar:
            t = bar.add_task(dest.name, total=total or None, completed=have if resumed else 0)
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)
                bar.update(t, advance=len(chunk))
    part.rename(dest)


# --- llama.cpp ---
def find_server(bindir: Path):
    for exe in ("llama-server.exe", "llama-server", "llama.exe", "llama"):
        hits = list(bindir.rglob(exe)) if bindir.exists() else []
        if hits:
            return hits[0]
    return None


def install_llama(cfg, root: Path):
    bindir = root / "bin" / "llama"
    if find_server(bindir):
        return
    tag, cuda = cfg["llama"]["release"], cfg["llama"]["cuda"]
    if sys.platform != "win32":
        build_llama(tag, bindir)
    else:
        base = f"https://github.com/ggml-org/llama.cpp/releases/download/{tag}/"
        for name in (f"llama-{tag}-bin-win-cuda-{cuda}-x64.zip", f"cudart-llama-bin-win-cuda-{cuda}-x64.zip"):
            z = root / "bin" / name
            download(base + name, z)
            zipfile.ZipFile(z).extractall(bindir)
    if not find_server(bindir):
        raise RuntimeError(f"no llama-server in {bindir}; the release layout changed, look inside and fix find_server")


def build_llama(tag, bindir: Path):
    """Linux: the release has no CUDA binary, so build llama-server from the tagged source. Needs git, cmake, nvcc."""
    src = bindir / "llama.cpp"
    if not (src / "CMakeLists.txt").exists():
        subprocess.run(["git", "clone", "--depth", "1", "--branch", tag, "https://github.com/ggml-org/llama.cpp", str(src)], check=True)
    subprocess.run(["cmake", "-B", "build", "-DGGML_CUDA=ON", "-DBUILD_SHARED_LIBS=OFF", "-DLLAMA_CURL=OFF",
                    "-DCMAKE_BUILD_TYPE=Release"], cwd=src, check=True)
    subprocess.run(["cmake", "--build", "build", "--config", "Release", "-j", "--target", "llama-server"], cwd=src, check=True)


# --- models ---
def mmproj_path(root: Path, m):
    return root / "models" / (Path(m["file"]).stem + "-mmproj.gguf")


def names_for_profile(cfg, profile):
    if profile == "all":
        return list(cfg["models"])
    names = []
    for spec in cfg["profiles"][profile].values():
        if spec.startswith("local/") and spec[6:] not in names:
            names.append(spec[6:])
    return names


def install_models(cfg, root: Path, names):
    hf = os.environ.get("HF_ENDPOINT", "https://huggingface.co").rstrip("/")
    for n in names:
        m = cfg["models"][n]
        download(f"{hf}/{m['repo']}/resolve/main/{m['file']}", root / "models" / m["file"])
        if m.get("mmproj"):
            download(f"{hf}/{m['repo']}/resolve/main/{m['mmproj']}", mmproj_path(root, m))


def write_presets(cfg, root: Path):
    """models/models.ini for `llama-server --models-preset`. Only models whose files exist go in."""
    lines = ["version = 1", "", "[*]", f"c = {cfg['llama']['ctx']}", "jinja = true", "n-gpu-layers = 999"]
    if cfg["llama"].get("threads"):
        lines.append(f"t = {cfg['llama']['threads']}")
    lines.append("")
    for n, m in cfg["models"].items():
        f = root / "models" / m["file"]
        if not f.exists():
            continue
        lines += [f"[{n}]", f"model = {f.as_posix()}"]
        if m.get("mmproj"):
            lines.append(f"mmproj = {mmproj_path(root, m).as_posix()}")
        if m.get("device") == "cpu":
            lines.append("n-gpu-layers = 0")
        if m.get("resident"):
            lines.append("load-on-startup = true")
        lines.append("")
    out = root / "models" / "models.ini"
    out.parent.mkdir(exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    return out
