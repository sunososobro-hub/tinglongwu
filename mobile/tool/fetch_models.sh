#!/usr/bin/env bash
# Downloads the phone models from their official sources into <DIR>/models (phone layout) and
# their license texts into <DIR>/licenses. make_models_zip.py then checks every file against
# assets/models_manifest.json, so a changed upstream file fails the build instead of shipping.
#
#   bash tool/fetch_models.sh <DIR>
set -euo pipefail
DIR="${1:?usage: fetch_models.sh <DIR>}"
M="$DIR/models"; L="$DIR/licenses"; T="$DIR/tmp"
mkdir -p "$M/gigaam3" "$L" "$T"
dl() { curl -fsSL --retry 3 --retry-delay 5 -o "$2" "$1"; }

GIGA=sherpa-onnx-nemo-transducer-punct-giga-am-v3-russian-2025-12-16
SHERPA=https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models
dl "$SHERPA/$GIGA.tar.bz2" "$T/giga.tar.bz2"
echo "f9620a0099019c6afcee26525ef9ed3297fa50dd5691c1902af0c948fc1a470b  $T/giga.tar.bz2" | sha256sum -c -
tar xjf "$T/giga.tar.bz2" -C "$T"
for f in encoder.int8.onnx decoder.onnx joiner.onnx tokens.txt; do cp "$T/$GIGA/$f" "$M/gigaam3/"; done
cp "$T/$GIGA/LICENSE" "$L/GigaAM-LICENSE.txt"

dl "$SHERPA/silero_vad.onnx" "$M/silero_vad.onnx"
dl https://raw.githubusercontent.com/snakers4/silero-vad/master/LICENSE "$L/silero-vad-LICENSE.txt"

HY=https://huggingface.co/tencent/Hy-MT2-1.8B-GGUF/resolve/main
dl "$HY/Hy-MT2-1.8B-Q4_K_M.gguf" "$M/Hy-MT2-1.8B-Q4_K_M.gguf"
dl "$HY/LICENSE.txt" "$L/Hy-MT2-LICENSE.txt"
rm -rf "$T"
