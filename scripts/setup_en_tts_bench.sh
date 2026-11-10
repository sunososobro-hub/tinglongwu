#!/usr/bin/env bash
# One-shot: install Kokoro + Supertonic in isolated venvs, fetch pinned models,
# verify hashes, then benchmark English TTS on CPU (offline).
#   bash scripts/setup_en_tts_bench.sh
# Results: printed RTF table + wav files in ~/bench_out
set -e
REPO="$(cd "$(dirname "$0")/.." && pwd)"
BENCH="$REPO/scripts/bench_en_tts.py"

# --- Kokoro (kokoro-onnx 0.6.1; pulls GPL espeak/phonemizer -> separate venv only) ---
[ -x ~/tts-kokoro/bin/python ] || python3.12 -m venv ~/tts-kokoro
~/tts-kokoro/bin/pip install -q --upgrade pip
~/tts-kokoro/bin/pip install -q kokoro-onnx==0.6.1
~/tts-kokoro/bin/pip freeze > ~/tts-kokoro/freeze.txt
mkdir -p ~/models/kokoro && cd ~/models/kokoro
K=https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0
[ -f kokoro-v1.0.int8.onnx ] || curl -fL -o kokoro-v1.0.int8.onnx "$K/kokoro-v1.0.int8.onnx"
[ -f voices-v1.0.bin ] || curl -fL -o voices-v1.0.bin "$K/voices-v1.0.bin"
[ "$(stat -c %s kokoro-v1.0.int8.onnx)" = 92361271 ] || { echo "kokoro onnx size mismatch"; exit 1; }
[ "$(stat -c %s voices-v1.0.bin)" = 28214398 ] || { echo "voices.bin size mismatch"; exit 1; }
sha256sum kokoro-v1.0.int8.onnx voices-v1.0.bin > SHA256SUMS

# --- Supertonic (supertonic 1.3.1, MIT code; weights OpenRAIL-M) ---
[ -x ~/tts-supertonic/bin/python ] || python3.12 -m venv ~/tts-supertonic
~/tts-supertonic/bin/pip install -q --upgrade pip
~/tts-supertonic/bin/pip install -q supertonic==1.3.1
~/tts-supertonic/bin/pip freeze > ~/tts-supertonic/freeze.txt
mkdir -p ~/models/supertonic-3/onnx ~/models/supertonic-3/voice_styles && cd ~/models/supertonic-3
B=https://huggingface.co/Supertone/supertonic-3/resolve/724fb5abbf5502583fb520898d45929e62f02c0b
for f in onnx/duration_predictor.onnx onnx/text_encoder.onnx onnx/vector_estimator.onnx onnx/vocoder.onnx \
         onnx/tts.json onnx/unicode_indexer.json voice_styles/M1.json voice_styles/M2.json \
         voice_styles/F1.json voice_styles/F2.json LICENSE; do
  [ -f "$f" ] || curl -fL -o "$f" "$B/$f"
done
cat > SHA256SUMS <<'EOF'
c3eb91414d5ff8a7a239b7fe9e34e7e2bf8a8140d8375ffb14718b1c639325db  onnx/duration_predictor.onnx
c7befd5ea8c3119769e8a6c1486c4edc6a3bc8365c67621c881bbb774b9902ff  onnx/text_encoder.onnx
883ac868ea0275ef0e991524dc64f16b3c0376efd7c320af6b53f5b780d7c61c  onnx/vector_estimator.onnx
085de76dd8e8d5836d6ca66826601f615939218f90e519f70ee8a36ed2a4c4ba  onnx/vocoder.onnx
EOF
sha256sum -c SHA256SUMS

# --- Bench (offline) ---
cd ~
set +e
for v in af_heart am_michael; do
  ~/tts-kokoro/bin/python "$BENCH" --engine kokoro --model-dir ~/models/kokoro --voice "$v"
done
for v in M1 F1; do
  ~/tts-supertonic/bin/python "$BENCH" --engine supertonic --model-dir ~/models/supertonic-3 --voice "$v"
done
echo "wav files: ~/bench_out   (play: aplay ~/bench_out/kokoro_af_heart_1.wav)"
