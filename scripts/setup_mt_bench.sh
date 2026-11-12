#!/usr/bin/env bash
# One-shot: isolated venv with llama-cpp-python (prebuilt CPU wheel), fetch pinned GGUFs, verify hashes,
# bench Hy-MT2-1.8B vs the repo's OPUS path on CPU (offline), print side-by-side table.
#   bash scripts/setup_mt_bench.sh
# Results: ~/mt_bench_{hymt,opus}.tsv
set -e
REPO="$(cd "$(dirname "$0")/.." && pwd)"
LLAMA_VER=0.3.36   # py3-none manylinux x86_64 wheel on the CPU index (no compiler needed)

[ -x ~/mt-bench/bin/python ] || python3.12 -m venv ~/mt-bench
~/mt-bench/bin/pip install -q --upgrade pip
~/mt-bench/bin/pip install -q --only-binary=:all: "llama-cpp-python==$LLAMA_VER" \
  --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu
~/mt-bench/bin/pip freeze > ~/mt-bench/freeze.txt

# --- Models (HF revisions pinned by commit) ---
mkdir -p ~/models/mt && cd ~/models/mt
H=https://huggingface.co/tencent/Hy-MT2-1.8B-GGUF/resolve/a0c709d9fac510f2c807aa3af52872340dc37a4a
[ -f Hy-MT2-1.8B-Q4_K_M.gguf ] || curl -fL -o Hy-MT2-1.8B-Q4_K_M.gguf "$H/Hy-MT2-1.8B-Q4_K_M.gguf"
cat > SHA256SUMS <<'EOF'
dc5f44fcf1fa496ee7ad725982c0c8c553a4de00259b53af84c4b89fb0c06699  Hy-MT2-1.8B-Q4_K_M.gguf
EOF
sha256sum -c SHA256SUMS

# --- Bench (offline) ---
cd ~
set +e
~/mt-bench/bin/python "$REPO/scripts/bench_zh_en_mt.py" --gguf ~/models/mt/Hy-MT2-1.8B-Q4_K_M.gguf --threads 8
"$REPO/.venv/bin/python" "$REPO/scripts/bench_opus_zh_en.py"

# --- Side-by-side ---
python3 - <<'PY'
import statistics
from pathlib import Path
h = Path.home()
cols = {}
for k in ("opus", "hymt"):
    p = h / f"mt_bench_{k}.tsv"
    cols[k] = [l.split("\t") for l in p.read_text("utf-8").splitlines()] if p.exists() else []
n = max(len(v) for v in cols.values())
print(f"{'zh':<22} | {'opus':<40} | {'hymt':<40}")
for i in range(n):
    zh = next((cols[k][i][0] for k in cols if i < len(cols[k])), "")
    cell = lambda k: (cols[k][i][1] if i < len(cols[k]) else "-")[:40]
    print(f"{zh:<22} | {cell('opus'):<40} | {cell('hymt'):<40}")
for k, v in cols.items():
    t = sorted(float(r[2]) for r in v)
    if t:
        print(f"{k}: p50={statistics.median(t):.2f}s p95={t[min(len(t)-1, round(.95*(len(t)-1)))]:.2f}s")
PY
