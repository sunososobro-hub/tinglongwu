#!/usr/bin/env bash
# tinglongWu helper menu (Linux). Run from the repo:  bash scripts/linux_menu.sh
# Paths can be overridden with env vars before running.
set -e
cd "$(dirname "$0")/.."
. .venv/bin/activate

VOSK_PY="${VOSK_PY:-$HOME/tts-vosk/bin/python}"
VOSK_MODEL="${VOSK_MODEL:-$HOME/vosk-model-tts-ru-0.7-multi}"
VOICE="${VOICE:-4}"
KOKORO_PY="${KOKORO_PY:-$HOME/tts-kokoro/bin/python}"
KOKORO_MODEL="${KOKORO_MODEL:-$HOME/models/kokoro}"
KOKORO_VOICE="${KOKORO_VOICE:-0}"

echo "tinglongWu"
echo "  Enter) 聽  英文 -> 中文字幕（預設）"
echo "  1)     聽  其他語言 -> 中文字幕"
echo "  2)     說  中文 -> 英文語音"
echo "  3)     說  中文 -> 俄文語音"
read -rp "> " pick
case "$pick" in
  "") mode=1; L=en ;;
  1) mode=1
     echo "語言代碼：ru 俄文、ja 日文、ko 韓文、de 德文、fr 法文、es 西文"
     read -rp "聽哪種語言 [Enter=en]: " L; L="${L:-en}" ;;
  2) mode=2; L=en ;;
  3) mode=2; L=ru ;;
  *) echo "沒有這個選項"; exit 1 ;;
esac
if [ "$mode" = "1" ]; then echo "聽  $L -> 中文字幕"; else echo "說  中文 -> $L 語音"; fi

have_pairs() {  # 0 if every OPUS pair is already on disk (no network)
  python - "$@" <<'PY'
import sys
from tinglongwu.models import models_dir
d = models_dir(None) / "opus"
sys.exit(0 if all((d / p).is_dir() for p in sys.argv[1:]) else 1)
PY
}

if [ "$mode" = "1" ]; then
  if [ "$L" = "en" ]; then need=(en-zh); setup=(--to zh); else need=("$L-en" en-zh); setup=(--langs "$L" --to zh); fi
  if ! have_pairs "${need[@]}"; then
    echo "還沒有 $L 的翻譯模型，現在連網下載（只有這一次）..."
    tlwu-setup "${setup[@]}"
  fi
  exec tlwu --lang "$L"
fi

if [ "$L" = "en" ]; then
  # zh->en is a direct OPUS pair; en-zh is only for the back-translation shown while confirming
  if ! have_pairs zh-en en-zh; then
    echo "還沒有 中文->英文 的翻譯模型，現在連網下載（只有這一次）..."
    tlwu-setup --pair zh-en --pair en-zh
  fi
  REV=(python -m tinglongwu.reverse.proto --mic --compute cpu --mt opus --to en
       --tts-engine kokoro --tts-python "$KOKORO_PY" --tts-model "$KOKORO_MODEL" --speaker "$KOKORO_VOICE")
  TXT=(python -m tinglongwu.reverse.proto --mic --no-tts --no-vmic --compute cpu --mt opus --to en)
else
  REV=(python -m tinglongwu.reverse.proto --mic --compute cpu --mt opus
       --tts-engine vosk --tts-python "$VOSK_PY" --tts-model "$VOSK_MODEL" --speaker "$VOICE")
  TXT=(python -m tinglongwu.reverse.proto --mic --no-tts --no-vmic --compute cpu --mt opus --to ru)
fi
echo "每句先確認：Enter=送出  x=丟掉  v0-v4=換聲音"
echo "  1) 送進 Meet（Meet 麥克風選 tlwu_mic）"
echo "  2) 送進 Meet，自己耳機也聽得到"
echo "  3) 不進 Meet，直接從耳機/喇叭念出來"
echo "  4) 只看翻譯文字（不發聲）"
read -rp "選 1-4: " out
AUTO=()
if [ "$out" != "4" ]; then
  read -rp "每句直接送、不確認？[Enter=要確認 / y=直接送]: " ac
  [ "$ac" = "y" ] && AUTO=(--no-confirm)
fi
case "$out" in
  1) exec "${REV[@]}" "${AUTO[@]}" ;;
  2) exec "${REV[@]}" "${AUTO[@]}" --preview-device Family ;;
  3) exec "${REV[@]}" "${AUTO[@]}" --device Family ;;
  4) exec "${TXT[@]}" ;;
  *) echo "沒有這個選項"; exit 1 ;;
esac
