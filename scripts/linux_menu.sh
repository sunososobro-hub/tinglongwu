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
echo "  1) 聽：電腦播的外語 -> 中文字幕（預設）"
echo "  2) 說：你講中文 -> 外語語音"
read -rp "選 1-2 [Enter=1]: " mode
mode="${mode:-1}"

echo "語言代碼：en 英文、ru 俄文、ja 日文、ko 韓文、de 德文、fr 法文、es 西文"
if [ "$mode" = "1" ]; then
  read -rp "聽哪種語言 [Enter=en]: " L; L="${L:-en}"
else
  read -rp "翻成哪種語言 [Enter=ru，目前只有 ru]: " L; L="${L:-ru}"
fi

if [ "$mode" = "1" ]; then
  read -rp "第一次用這個語言？要先連網下載翻譯模型 [y/N]: " d
  if [ "$d" = "y" ]; then tlwu-setup --langs "$L"; fi
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
