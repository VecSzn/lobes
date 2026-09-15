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
# the reasoning model gets workers x its thinking cap of context (8f7da9e); the 4070 stays at the default
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
# v2 is the branch of that name: the v2 commit plus the seed fix (6c2a145) cherry-picked, nothing else from main.
git stash -q && git checkout -q v2 && git stash pop -q
lobes eval --conditions A,D,E --tag 5090-v2 > eval-v2.log 2>&1
lobes eval --conditions B,C --tag 5090-v2 > eval-v2-bc.log 2>&1
lobes eval --conditions D --seeds 0 --effort high --tag 5090-v2-high > eval-v2-high.log 2>&1
# main carries the forced answer (3655e67), so R runs with it and v2 without.
# R actually ran on a second box set up by the lines above `git stash` (REPORT, deviation 9).
git stash -q && git checkout -q main && git stash pop -q
lobes eval --conditions R --tag 5090-v2 > eval-r.log 2>&1      # the 9B alone; same dir so one report shows it next to A-E
# v3 (PREREG-v3): the enlarged suites, the 9B alone and the pre-v3 medium on box 1, v3 medium then high on box 2.
# fc7ff7e is the pre-v3 runtime plus the enlarged suites and the new N, so the comparison line reads the same items.
git checkout -q fc7ff7e
lobes eval --conditions D --seeds 0 --tag 5090-pre-v3 > eval-pre-v3.log 2>&1
git checkout -q main
lobes eval --conditions R --seeds 0 --suites gsm8k,humaneval,tools,simpleqa,multistep --tag 5090-v3 > eval-v3-r.log 2>&1
lobes eval --conditions D --seeds 0 --tag 5090-v3 > eval-v3.log 2>&1                      # box 2 from here
lobes eval --conditions D --seeds 0 --effort high --tag 5090-v3-high > eval-v3-high.log 2>&1
# The intake fix and the executive swap (0f218c1, 4a98642) landed after box 2 had finished medium and 89 items of high
# on d947c71. Those results keep their commit in the directory name; both conditions ran again on main, the high one
# on box 1 once its R run was through. Same two sed lines as above, then lobes install and a fresh lobes serve.
mv eval/results/5090-v3 eval/results/5090-v3-d947c71 && mv eval/results/5090-v3-high eval/results/5090-v3-high-d947c71
git checkout HEAD -- lobes.yaml && git merge --ff-only origin/main
lobes eval --conditions D --seeds 0 --tag 5090-v3 > eval-v3.log 2>&1                      # box 2
lobes eval --conditions D --seeds 0 --effort high --tag 5090-v3-high > eval-v3-high.log 2>&1   # box 1
# The second version at high on the enlarged suites, with the same intake fix: branch v2-fix is fc7ff7e plus the
# model-only classifier, granite-4.0-h-1b as executive and the language-lobe fixes (1d778d1). Box 2 after its v3 medium.
git checkout v2-fix
lobes eval --conditions D --seeds 0 --effort high --tag 5090-v2fix-high > eval-v2fix-high.log 2>&1
