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
# the reasoning model gets workers x its thinking cap of context (per-model ctx, main); the 4070 stays at the default
sed -i '/^  qwen3.5-4b:$/,/^  qwen3.5-4b-vl:$/s/^    thinking: true$/    thinking: true\n    ctx: 65536/' lobes.yaml
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
# v2 is the branch of that name: the v2 commit plus the seed fix (seed + call index) cherry-picked, nothing else from main.
git stash -q && git checkout -q v2 && git stash pop -q
lobes eval --conditions A,D,E --tag 5090-v2 > eval-v2.log 2>&1
lobes eval --conditions B,C --tag 5090-v2 > eval-v2-bc.log 2>&1
lobes eval --conditions D --seeds 0 --effort high --tag 5090-v2-high > eval-v2-high.log 2>&1
# main carries the forced answer, so R runs with it and v2 without.
# R actually ran on a second box set up by the lines above `git stash` (REPORT, deviation 9).
git stash -q && git checkout -q main && git stash pop -q
lobes eval --conditions R --tag 5090-v2 > eval-r.log 2>&1      # the 9B alone; same dir so one report shows it next to A-E
# v3 (PREREG-v3): the enlarged suites, four items in flight everywhere (REPORT, deviation 11). The 9B alone and the
# second version at medium on box 1, v3 medium then high on box 2. The second-version line ran on the pre-v3 runtime
# with the enlarged suites added; that tree is not a commit of the rewritten history (it is branch v2-fix before its
# intake fixes), the results are in eval/results/5090-pre-v3.
lobes eval --conditions D --seeds 0 --workers 4 --tag 5090-pre-v3 > eval-pre-v3.log 2>&1
git checkout -q main
lobes eval --conditions R --seeds 0 --workers 4 --suites gsm8k,humaneval,tools,simpleqa,multistep --tag 5090-v3 > eval-v3-r.log 2>&1
# v3 went through six trees during 09-14 evening and 09-15, each folded into main's last two commits; the partial
# runs of every one are kept under their own directory and read in REPORT (deviations 10 and 14): 5090-v3-old-intake
# (rules in the classifier, 1.2B executive), 5090-v3-last-number (agreement on the last number only, the full 370),
# 5090-v3-values (one value per line), 5090-v3-cheap-first and -cheap-first-2 (the reasoning lobe thinking only on
# disagreement), 5090-v3-settle and 5090-v3-settle-words (a program that ran cannot be outvoted; whole-word match).
# The lines below are main as shipped. Same two sed lines as above, then lobes install and a fresh lobes serve.
lobes eval --conditions D --seeds 0 --workers 4 --tag 5090-v3 > eval-v3.log 2>&1                      # box 1
lobes eval --conditions D --seeds 0 --workers 4 --effort high --tag 5090-v3-high > eval-v3-high.log 2>&1   # box 2
# The second version at high on the enlarged suites, with the same intake fix: branch v2-fix is that runtime plus the
# model-only classifier, granite-4.0-h-1b as executive and the language-lobe fixes. Box 2 before its v3 high.
git checkout -q v2-fix
lobes eval --conditions D --seeds 0 --workers 4 --effort high --tag 5090-v2fix-high > eval-v2fix-high.log 2>&1
