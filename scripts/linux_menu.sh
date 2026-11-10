#!/usr/bin/env bash
# tinglongWu helper menu (Linux). Run from the repo:  bash scripts/linux_menu.sh
# Paths can be overridden with env vars before running.
set -e
cd "$(dirname "$0")/.."
. .venv/bin/activate

VOSK_PY="${VOSK_PY:-$HOME/tts-vosk/bin/python}"
VOSK_MODEL="${VOSK_MODEL:-$HOME/vosk-model-tts-ru-0.7-multi}"
VOICE="${VOICE:-4}"

echo "tinglongWu"
echo "  1) 聽  外語 -> 字幕：中文（預設）"
echo "  2) 說  中文 -> 語音：外語"
read -rp "選 1-2 [Enter=1]: " mode
mode="${mode:-1}"

echo "語言代碼：en 英文、ru 俄文、ja 日文、ko 韓文、de 德文、fr 法文、es 西文"
if [ "$mode" = "1" ]; then
  read -rp "聽哪種語言 [Enter=en]: " L; L="${L:-en}"
  echo "聽  $L -> 字幕：中文"
else
  read -rp "說成哪種語言 [Enter=ru，目前只有 ru]: " L; L="${L:-ru}"
  echo "說  中文 -> 語音：$L"
fi

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

if [ "$mode" != "2" ]; then echo "沒有這個選項"; exit 1; fi
if [ "$L" != "ru" ]; then
  echo "「說」目前只支援 ru（俄文）；其他語言還沒有語音模型。"
  exit 1
fi

REV=(python -m tinglongwu.reverse.proto --mic --compute cpu --mt opus
     --tts-engine vosk --tts-python "$VOSK_PY" --tts-model "$VOSK_MODEL" --speaker "$VOICE")
echo "每句先確認：Enter=送出  x=丟掉  v0-v4=換聲音"
echo "  1) 送進 Meet（Meet 麥克風選 tlwu_mic）"
echo "  2) 送進 Meet，自己耳機也聽得到"
echo "  3) 不進 Meet，直接從耳機/喇叭念出來"
echo "  4) 只看翻譯文字（不發聲）"
read -rp "選 1-4: " out
case "$out" in
  1) exec "${REV[@]}" ;;
  2) exec "${REV[@]}" --preview-device Family ;;
  3) exec "${REV[@]}" --device Family ;;
  4) exec python -m tinglongwu.reverse.proto --mic --no-tts --no-vmic --compute cpu --mt opus ;;
  *) echo "沒有這個選項"; exit 1 ;;
esac
