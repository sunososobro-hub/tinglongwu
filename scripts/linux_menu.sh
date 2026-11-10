#!/usr/bin/env bash
# tinglongWu helper menu (Linux). Run from the repo:  bash scripts/linux_menu.sh
# Paths can be overridden with env vars before running; WHISPER sets the speech model, e.g.  WHISPER=medium bash scripts/linux_menu.sh
set -e
cd "$(dirname "$0")/.."
. .venv/bin/activate

VOSK_PY="${VOSK_PY:-$HOME/tts-vosk/bin/python}"
VOSK_MODEL="${VOSK_MODEL:-$HOME/vosk-model-tts-ru-0.7-multi}"
WHISPER="${WHISPER:-}"   # empty = program default
W_LISTEN=(); W_SPEAK=()
if [ -n "$WHISPER" ]; then
  W_LISTEN=(--model "$WHISPER"); W_SPEAK=(--whisper "$WHISPER")
  if ! python -c "import sys; from tinglongwu.models import models_dir; sys.exit(0 if (models_dir(None) / 'whisper-$WHISPER').is_dir() else 1)"; then
    echo "還沒有語音模型 $WHISPER，現在連網下載（只有這一次）..."
    tlwu-setup --whisper "$WHISPER"
  fi
fi
VOICE="${VOICE:-4}"
KOKORO_PY="${KOKORO_PY:-$HOME/tts-kokoro/bin/python}"
KOKORO_MODEL="${KOKORO_MODEL:-$HOME/models/kokoro}"
KOKORO_VOICE="${KOKORO_VOICE:-0}"

L="${TLWU_LANG:-en}"
lang_name() { case "$1" in en) echo 英文;; ru) echo 俄文;; ja) echo 日文;; ko) echo 韓文;; de) echo 德文;; fr) echo 法文;; es) echo 西文;; *) echo "$1";; esac; }
BOTH=0
while :; do
  N="$(lang_name "$L")"
  echo
  echo "tinglongWu    對方語言：$N    （按 L 可改語言）"
  echo "  1) 開會  聽＋說，送進 Meet（要戴耳機麥克風）"
  echo "  2) 純說  中文 -> ${N}語音"
  echo "  3) 純聽  ${N} -> 中文字幕"
  echo "  L) 改語言"
  read -rp "> " pick
  case "$pick" in
    ""|1) mode=2; BOTH=1 ;;
    2) mode=2 ;;
    3) mode=1 ;;
    l|L) echo "語言代碼：en 英文、ru 俄文、ja 日文、ko 韓文、de 德文、fr 法文、es 西文（開會/純說目前只支援 en、ru）"
         read -rp "對方說哪種語言 [Enter=en]: " L; L="${L:-en}"; continue ;;
    *) echo "沒有這個選項"; continue ;;
  esac
  if [ "$mode" = "2" ] && [ "$L" != "en" ] && [ "$L" != "ru" ]; then
    echo "$N 目前只能「純聽」；開會/純說只支援英文、俄文。"; continue
  fi
  break
done
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
  exec tlwu --lang "$L" "${W_LISTEN[@]}"
fi

if [ "$L" = "en" ]; then
  # zh->en is a direct OPUS pair; en-zh is only for the back-translation shown while confirming
  if ! have_pairs zh-en en-zh; then
    echo "還沒有 中文->英文 的翻譯模型，現在連網下載（只有這一次）..."
    tlwu-setup --pair zh-en --pair en-zh
  fi
  REV=(python -m tinglongwu.reverse.proto --mic --compute cpu --mt opus --to en
       --tts-engine kokoro --tts-python "$KOKORO_PY" --tts-model "$KOKORO_MODEL" --speaker "$KOKORO_VOICE" "${W_SPEAK[@]}")
  TXT=(python -m tinglongwu.reverse.proto --mic --no-tts --no-vmic --compute cpu --mt opus --to en "${W_SPEAK[@]}")
else
  REV=(python -m tinglongwu.reverse.proto --mic --compute cpu --mt opus
       --tts-engine vosk --tts-python "$VOSK_PY" --tts-model "$VOSK_MODEL" --speaker "$VOICE" "${W_SPEAK[@]}")
  TXT=(python -m tinglongwu.reverse.proto --mic --no-tts --no-vmic --compute cpu --mt opus --to ru "${W_SPEAK[@]}")
fi

if [ "$BOTH" = "1" ]; then
  if [ "$L" = "ru" ] && ! have_pairs ru-en en-zh; then
    echo "還沒有 ru 的翻譯模型，現在連網下載（只有這一次）..."
    tlwu-setup --langs ru --to zh
  fi
  read -rp "每句直接送、不確認？[Enter=要確認 / y=直接送]: " ac
  AUTO=(); [ "$ac" = "y" ] && AUTO=(--no-confirm)
  # listen in the background (no keyboard), speak in the foreground; Ctrl+C stops both
  tlwu --lang "$L" "${W_LISTEN[@]}" < /dev/null &
  LISTEN_PID=$!
  trap 'kill $LISTEN_PID 2>/dev/null' EXIT INT TERM
  echo "開會模式：[$L]/[ZH] 是對方的話；你講中文會送進 Meet（麥克風選 tlwu_mic）。請戴耳機。"
  "${REV[@]}" "${AUTO[@]}"
  exit
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
