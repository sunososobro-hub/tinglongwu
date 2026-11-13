#!/usr/bin/env bash
# Downloads the phone models from their official sources into <DIR>/models (phone layout) and
# their license texts into <DIR>/licenses. make_models_zip.py then checks every file against
# assets/models_manifest.json, so a changed upstream file fails the build instead of shipping.
#
#   bash tool/fetch_models.sh <DIR>
set -euo pipefail
DIR="${1:?usage: fetch_models.sh <DIR>}"
M="$DIR/models"; L="$DIR/licenses"; T="$DIR/tmp"
mkdir -p "$M/sensevoice" "$L" "$T"
dl() { curl -fsSL --retry 3 --retry-delay 5 -o "$2" "$1"; }

SV=sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17
SHERPA=https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models
dl "$SHERPA/$SV.tar.bz2" "$T/sv.tar.bz2"
echo "7d1efa2138a65b0b488df37f8b89e3d91a60676e416f515b952358d83dfd347e  $T/sv.tar.bz2" | sha256sum -c -
tar xjf "$T/sv.tar.bz2" -C "$T"
for f in model.int8.onnx tokens.txt; do cp "$T/$SV/$f" "$M/sensevoice/"; done
cp "$T/$SV/LICENSE" "$L/SenseVoice-LICENSE.txt"

dl "$SHERPA/silero_vad.onnx" "$M/silero_vad.onnx"
dl https://raw.githubusercontent.com/snakers4/silero-vad/master/LICENSE "$L/silero-vad-LICENSE.txt"

HY=https://huggingface.co/tencent/Hy-MT2-1.8B-GGUF/resolve/main
dl "$HY/Hy-MT2-1.8B-Q4_K_M.gguf" "$M/Hy-MT2-1.8B-Q4_K_M.gguf"
dl "$HY/LICENSE.txt" "$L/Hy-MT2-LICENSE.txt"
rm -rf "$T"
