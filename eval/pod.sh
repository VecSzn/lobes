#!/bin/sh -e
# One rented GPU box (RunPod pytorch template, ubuntu 24.04, cuda 12.8), repo already at /workspace/lobes.
# Builds llama-server, pulls every model, then runs the v1 tag and v2 into separate result dirs.
cd /workspace/lobes
export PATH=/usr/local/cuda/bin:$PATH
apt-get update -q && apt-get install -y -q cmake git build-essential
pip install -e '.[dev,eval,ocr]'
lobes install --profile all
# 32 GB card: keep everything loaded. Working-copy only, not committed; the 4070 numbers used 6400.
sed -i 's/vram_budget_mb: 6400/vram_budget_mb: 28000/' lobes.yaml
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
lobes eval --conditions A,D,E --tag 5090-v2 > eval-v2.log 2>&1
lobes eval --conditions D --seeds 0 --effort high --tag 5090-v2-high > eval-v2-high.log 2>&1
# v3 (reflect, search, auto) leaves medium alone, so only high and auto are run. main must be at v3 by here.
lobes eval --conditions D --seeds 0 --effort high --tag 5090-v3-high > eval-v3-high.log 2>&1
lobes eval --conditions D --seeds 0 --effort auto --tag 5090-v3-auto > eval-v3-auto.log 2>&1
