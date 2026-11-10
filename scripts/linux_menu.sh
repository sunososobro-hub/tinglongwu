#!/usr/bin/env bash
# tinglongWu helper menu (Linux). Run from the repo:  bash scripts/linux_menu.sh
# Paths can be overridden with env vars before running.
set -e
cd "$(dirname "$0")/.."
. .venv/bin/activate

VOSK_PY="${VOSK_PY:-$HOME/tts-vosk/bin/python}"
VOSK_MODEL="${VOSK_MODEL:-$HOME/vosk-model-tts-ru-0.7-multi}"
VOICE="${VOICE:-4}"
REV=(python -m tinglongwu.reverse.proto --mic --compute cpu --mt opus
     --tts-engine vosk --tts-python "$VOSK_PY" --tts-model "$VOSK_MODEL" --speaker "$VOICE")

cat <<'EOF'
tinglongWu
  聽（電腦播的聲音 -> 中文字幕）
    1) 英文
    2) 俄文
  說（你講中文 -> 俄文語音；每句先確認，Enter=送出 x=丟掉 v0-v4=換聲音）
    3) 送進 Meet（Meet 麥克風選 tlwu_mic）
    4) 送進 Meet，自己耳機也聽得到
    5) 不進 Meet，直接從耳機/喇叭念出來
  6) 只看翻譯文字（不發聲）
EOF
read -rp "選 1-6: " c
case "$c" in
  1) exec tlwu --lang en ;;
  2) exec tlwu --lang ru ;;
  3) exec "${REV[@]}" ;;
  4) exec "${REV[@]}" --preview-device Family ;;
  5) exec "${REV[@]}" --device Family ;;
  6) exec python -m tinglongwu.reverse.proto --mic --no-tts --no-vmic --compute cpu --mt opus ;;
  *) echo "沒有這個選項"; exit 1 ;;
esac
