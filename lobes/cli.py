import subprocess
import sys
from pathlib import Path

import typer
from rich import print as rprint
from rich.markup import escape
from rich.table import Table

from . import config, install, providers, runner, server
from . import eval as evals
from .models import ModelManager

app = typer.Typer(add_completion=False, no_args_is_help=True)
prov_app = typer.Typer(no_args_is_help=True)
app.add_typer(prov_app, name="providers")


@app.command("install")
def install_(profile: str = typer.Option(None, "--profile", help="which profile's models to fetch, or 'all'"),
             skip_llama: bool = False):
    """Download llama.cpp and the models a profile needs, write models/models.ini."""
    cfg = config.load()
    root = cfg["_root"]
    if not skip_llama:
        install.install_llama(cfg, root)
    names = install.names_for_profile(cfg, profile or cfg["profile"])
    install.install_models(cfg, root, names)
    out = install.write_presets(cfg, root)
    rprint(f"wrote {out}")


def _server_cmd(cfg):
    root = cfg["_root"]
    exe = install.find_server(root / "bin" / "llama")
    if not exe:
        raise typer.BadParameter("llama-server not found, run `lobes install` first")
    cmd = [str(exe)]
    if exe.stem == "llama":            # newer releases ship one binary with subcommands
        cmd.append("serve")
    ini = install.write_presets(cfg, root)
    return cmd + ["--models-preset", str(ini), "--models-max", "8",
                  "--host", cfg["llama"]["host"], "--port", str(cfg["llama"]["port"])]


@app.command()
def serve(extra: list[str] = typer.Argument(None)):
    """Run llama-server in router mode in the foreground."""
    cfg = config.load()
    cmd = _server_cmd(cfg) + list(extra or [])
    rprint("[dim]" + " ".join(cmd) + "[/dim]")
    raise SystemExit(subprocess.call(cmd))


@app.command()
def models():
    """What the router knows, what is loaded, and what nvidia-smi says."""
    cfg = config.load()
    mm = ModelManager(cfg)
    if not mm.alive():
        rprint("[red]llama-server is not running[/red] (lobes serve)")
        raise typer.Exit(1)
    t = Table("model", "status", "vram_mb (yaml)")
    for name, st in mm.status().items():
        t.add_row(name, st, str(cfg["models"].get(name, {}).get("vram_mb", "?")))
    rprint(t)
    rprint(f"loaded by yaml estimate: {mm.used_mb()} / {mm.budget} MB   nvidia-smi used: {mm.vram_now_mb()} MB")


@app.command()
def load(name: str):
    cfg = config.load()
    ms = ModelManager(cfg).ensure(name)
    rprint(f"{name} loaded in {ms} ms, gpu now {ModelManager.vram_now_mb()} MB")


@app.command()
def unload(name: str):
    cfg = config.load()
    ms = ModelManager(cfg).unload(name)
    rprint(f"{name} unloaded in {ms} ms, gpu now {ModelManager.vram_now_mb()} MB")


@app.command()
def ask(prompt: str,
        lobe: str = typer.Option(None, help="talk to one lobe directly instead of running the full loop"),
        profile: str = None,
        think: bool = typer.Option(None, "--think/--no-think"),
        image: list[Path] = typer.Option(None),
        effort: str = typer.Option(None, help="low, medium or high; bounded calls, no automatic escalation"),
        max_tokens: int = 2048):
    cfg = config.load()
    if effort:
        cfg["effort"] = effort
    if lobe is None:
        state = runner.run(cfg, prompt, profile=profile, images=image)
        print(state.answer)           # model text is not rich markup: [n // 2] would vanish
        for uncertainty in state.uncertainties:
            print(f"Note: {uncertainty}", file=sys.stderr)
        rprint(f"[dim]{state.summary()}[/dim]")
        return
    prov, model = config.lobe(cfg, lobe, profile)
    if prov == "impl":
        raise typer.BadParameter(f"{lobe} is '{model}' in this profile, not a model")
    if prov == "local":
        ModelManager(cfg).ensure(model)
    r = providers.chat(cfg["providers"][prov], model, [{"role": "user", "content": prompt}], images=image,
                       thinking=think if cfg["models"].get(model, {}).get("thinking") else None,
                       max_tokens=max_tokens)
    if r.reasoning:
        rprint(f"[dim]{escape(r.reasoning)}[/dim]")
    print(r.text)
    rprint(f"[dim]{r.ms} ms, usage={r.usage}[/dim]")


@app.command("eval")
def eval_(conditions: str = "R,A,B,B3,C,D", seeds: str = "0,1,2", suites: str = None,
          quick: bool = typer.Option(False, help="3 items per suite, for timing"),
          tag: str = typer.Option("", help="subdirectory of eval/results, one per code version or machine"),
          effort: str = typer.Option(None, help="low, medium or high, for every condition; default is effort in lobes.yaml"),
          ids: str = typer.Option(None, help="comma-separated item ids; only these run"),
          workers: int = typer.Option(1, help="items run at once; more than 1 only where every model stays loaded"),
          report: bool = typer.Option(False, help="print the tables from eval/results instead of running")):
    if report:
        print(evals.report(quick, tag))
        return
    cfg = config.load()
    if effort:
        cfg["effort"] = effort
    evals.main(cfg, conditions.split(","), [int(x) for x in seeds.split(",")], quick,
               suites.split(",") if suites else None, tag, workers, set(ids.split(",")) if ids else None)


@app.command()
def api(host: str = "127.0.0.1", port: int = 8090, profile: str = None):
    """OpenAI-compatible /v1/chat/completions in front of the runner; model "lobes/<profile>" picks the profile."""
    rprint(f"lobes api on http://{host}:{port}/v1  (llama-server must be running)")
    server.serve(config.load(), host, port, profile)


@prov_app.command("test")
def providers_test():
    cfg = config.load()
    for name, p in cfg["providers"].items():
        try:
            ids = providers.list_models(p)
            rprint(f"{name}: ok, {len(ids)} models" + (f" ({', '.join(ids[:5])}...)" if ids else ""))
        except Exception as e:  # noqa: BLE001 - any failure is the answer here
            rprint(f"{name}: [red]{type(e).__name__}: {e}[/red]")


if __name__ == "__main__":
    sys.exit(app())
