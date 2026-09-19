"""Questions about a python package sitting on disk that no single context here can hold: 15 files and about
33k tokens against the 16k every lobe runs at. Answering one means finding the right file before reading it.

Every answer is computed from the source, never typed in, the way eval/suites/make.py does it. The package is
this one, copied once into the data dir with the evaluation code left out, so the copy holds no script that
derives the answers. It is in no training set: the repository is private.
Asked these 59 questions with no package to read and no tools, qwen3.5-4b got 1 right, so they cannot be guessed.
"""
import ast
import shutil
from pathlib import Path

from lobes import config

SKIP = {"eval.py", "hard", "__pycache__", "__init__.py"}
PER_KIND = 20               # how many of each question, so one easy kind cannot carry the score


def corpus(data):
    """-> the package copied into the data dir once. Delete the directory to take a fresh snapshot."""
    root = data / "repo"
    if not root.exists():
        shutil.copytree(Path(config.__file__).parent, root,
                        ignore=lambda d, names: [n for n in names if n in SKIP or n.endswith(".pyc")])
    return root


def _read(root):
    """The answer to a where question is a file name, so two files sharing one would make it ambiguous."""
    files = [p for p in sorted(root.rglob("*.py")) if "__pycache__" not in p.parts]
    if len({p.name for p in files}) != len(files):
        raise ValueError(f"two files in {root} share a name")
    return [(p.name, ast.parse(p.read_text(encoding="utf-8"))) for p in files]


def _walk(tree, path, defs, consts, calls):
    """Collects what each question kind needs from one file, in one pass."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defs.setdefault(node.name, []).append((path, node))
        elif isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) and node.targets[0].id.isupper():
            try:
                consts.setdefault(node.targets[0].id, []).append(ast.literal_eval(node.value))
            except ValueError:                  # built from other names, so there is nothing to ask for
                consts.setdefault(node.targets[0].id, []).append(None)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            calls.setdefault(node.func.id, []).append(path)


def load(data):
    root = corpus(data)
    defs, consts, calls = {}, {}, {}
    for path, tree in _read(root):
        _walk(tree, path, defs, consts, calls)
    one = {n: w[0] for n, w in defs.items() if len(w) == 1 and not n.startswith("__") and len(n) > 3}

    where = [(n, w[0]) for n, w in sorted(one.items())]
    value = [(n, v[0]) for n, v in sorted(consts.items())
             if len(v) == 1 and isinstance(v[0], (int, float, str)) and 0 < len(str(v[0])) < 40 and len(n) > 2]
    # a name called from exactly one file other than the one defining it: neither that file nor the name says which
    other = [(n, sorted(set(calls.get(n, [])) - {w[0]})[0]) for n, w in sorted(one.items())
             if len(set(calls.get(n, [])) - {w[0]}) == 1]
    last = [(n, w[1].args.args[-1].arg) for n, w in sorted(one.items())
            if isinstance(w[1], (ast.FunctionDef, ast.AsyncFunctionDef)) and len(w[1].args.args) > 2]

    kinds = [
        ("where", where, "Which file defines `{}`? Answer with the file name."),
        ("value", value, "What value is the constant `{}` set to? Answer with the value."),
        ("calls", other, "One file other than the one defining `{}` calls it. Which file? Answer with the file name."),
        ("param", last, "What is the name of the last parameter of `{}`? Answer with the name."),
    ]
    items = []
    for kind, pairs, ask in kinds:
        step = max(1, len(pairs) // PER_KIND)   # spread over the package instead of taking the first twenty
        for name, gold in pairs[::step][:PER_KIND]:
            items.append({"id": f"repo-{kind}-{name}", "gold": str(gold),
                          "prompt": f"There is a python package at {root}. {ask.format(name)}"})
    return items
