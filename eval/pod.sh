#!/bin/sh -e
# One rented GPU box (RunPod pytorch template, ubuntu 24.04, cuda 12.8), repo already at /workspace/lobes.
# Builds llama-server, pulls every model, then runs the v1 tag and the v2 branch into separate result dirs, and R from main.
cd /workspace/lobes
export PATH=/usr/local/cuda/bin:$PATH
apt-get update -q && apt-get install -y -q cmake git build-essential
pip install -e '.[dev,eval,ocr]'
# 32 GB card: keep everything loaded and put the executive on the GPU too (its cgroup gives 40 cores but
# llama.cpp sees 384 threads and took 4.3 s a call on the CPU). Working-copy only; the 4070 numbers used 6400.
sed -i -e 's/vram_budget_mb: 6400/vram_budget_mb: 28000/' -e '/device: cpu/d' -e 's/vram_mb: 0$/vram_mb: 1500/' \
       -e 's/^  ctx: 16384$/  ctx: 16384\n  threads: 8/' lobes.yaml
lobes install --profile all
pytest -q
nohup lobes serve > serve.log 2>&1 &
sleep 20
lobes ask "what is 17 * 23"
# v1 at its tag (code only; lobes.yaml keeps the edit above), then back to main for v2.
# v1 has no --tag and resumes from eval/results, which at the tag holds only quick-* files.
git stash -q && git checkout -q v1-4070 && git stash pop -q
lobes eval --conditions A,D,E > eval-v1.log 2>&1
mkdir -p /workspace/5090-v1 && mv eval/results/[ADE]-s*.jsonl /workspace/5090-v1/
git stash -q && git checkout -q main && git stash pop -q
mkdir -p eval/results/5090-v1 && mv /workspace/5090-v1/*.jsonl eval/results/5090-v1/
# v2 is the branch of that name: the v2 commit plus the seed fix (6c2a145) cherry-picked, nothing else from main.
git stash -q && git checkout -q v2 && git stash pop -q
lobes eval --conditions A,D,E --tag 5090-v2 > eval-v2.log 2>&1
lobes eval --conditions B,C --tag 5090-v2 > eval-v2-bc.log 2>&1
lobes eval --conditions D --seeds 0 --effort high --tag 5090-v2-high > eval-v2-high.log 2>&1
# main carries the forced answer (3655e67), so R runs with it and v2 without.
# R actually ran on a second box set up by the lines above `git stash` (REPORT, deviation 9).
git stash -q && git checkout -q main && git stash pop -q
lobes eval --conditions R --tag 5090-v2 > eval-r.log 2>&1      # the 9B alone; same dir so one report shows it next to A-E
