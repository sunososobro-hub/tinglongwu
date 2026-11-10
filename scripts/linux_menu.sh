#!/usr/bin/env bash
# tinglongWu helper menu (Linux). Run from the repo:  bash scripts/linux_menu.sh
# Paths can be overridden with env vars before running; WHISPER sets the speech model, e.g.  WHISPER=medium bash scripts/linux_menu.sh
set -e
cd "$(dirname "$0")/.."
. .venv/bin/activate
IS_MAC=0; [ "$(uname -s)" = "Darwin" ] && IS_MAC=1

# quiet self-update (git pull --ff-only, ~6 s cap, never blocks); restart once on this script if it changed
if [ -f scripts/auto_update.sh ]; then
  . scripts/auto_update.sh
  if [ -z "${TLWU_UPDATED_ONCE:-}" ]; then
    tlwu_auto_update || true
    if [ -n "$TLWU_UPDATED" ]; then export TLWU_UPDATED_ONCE=1; exec bash "$0" "$@"; fi
  fi
fi

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

L="${TLWU_LANG:-auto}"
# macOS install skips the Russian voice: default to English unless the Vosk model is there
if [ "$IS_MAC" = "1" ] && [ -z "${TLWU_LANG:-}" ] && [ ! -d "$VOSK_MODEL" ]; then L=en; fi
CANDS="en,ru,ja,zh"   # auto: language follows whoever speaks, per sentence
FOLLOW_LANGS="en,ru,ja"   # languages the speak side prepares in meeting mode
FOLLOW_FILE="${XDG_RUNTIME_DIR:-${TMPDIR:-/tmp}}/tlwu_follow_lang"
lang_name() { case "$1" in auto) echo "自動（英/俄/日）";; en) echo 英文;; ru) echo 俄文;; ja) echo 日文;; ko) echo 韓文;; de) echo 德文;; fr) echo 法文;; es) echo 西文;; *) echo "$1";; esac; }
BOTH=0
speak_ok() { case "$1" in auto|en|ru|ja) return 0;; *) return 1;; esac; }

# Japanese needs Hy-MT2 (llama-cpp-python in .venv + the GGUF) and, for speaking, misaki[ja] in the Kokoro venv.
HYMT_FILE_REL="hymt2-1.8b/Hy-MT2-1.8B-Q4_K_M.gguf"
have_hymt_file() { python -c "import sys; from tinglongwu.models import models_dir; sys.exit(0 if (models_dir(None) / '$HYMT_FILE_REL').is_file() else 1)"; }
have_llama() { python -c "import llama_cpp" 2>/dev/null; }
hy_ready() { have_hymt_file && have_llama; }   # then ja is translated directly (no OPUS pivot)
ja_missing() {  # prints the install commands the Japanese voice/translator still needs; returns 1 if any
  local out=""
  have_llama || out+="  pip install llama-cpp-python --only-binary=:all: --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu   （裝在 .venv，已啟用時直接貼）"$'\n'
  "$KOKORO_PY" -c "import misaki.ja, fugashi, jaconv, unidic_lite" 2>/dev/null || \
    out+="  $KOKORO_PY -m pip install misaki==0.9.4 pyopenjtalk-plus fugashi unidic-lite mojimoji jaconv addict regex"$'\n'
  [ -z "$out" ] && return 0
  printf '%s' "$out"; return 1
}
while :; do
  N="$(lang_name "$L")"
  NS=""; speak_ok "$L" || NS="（${N}暫不支援）"
  echo
  echo "tinglongWu    對方語言：$N    （按 L 可改語言）"
  echo "  1) 開會  聽＋說，送進 Meet（要戴耳機麥克風）$NS"
  if [ "$L" = "auto" ]; then echo "  2) 純說  中文 -> 英文/俄文/日文語音"; else echo "  2) 純說  中文 -> ${N}語音$NS"; fi
  echo "  3) 純聽  ${N} -> 中文字幕"
  echo "  L) 改語言"
  read -rp "> " pick
  case "$pick" in
    ""|1) mode=2; BOTH=1 ;;
    2) mode=2 ;;
    3) mode=1 ;;
    l|L) echo "語言代碼：auto 自動（英/俄/日）、en 英文、ru 俄文、ja 日文、ko 韓文、de 德文、fr 法文、es 西文（開會/純說目前只支援 auto、en、ru、ja）"
         read -rp "對方說哪種語言 [Enter=auto]: " L; L="${L:-auto}"; continue ;;
    *) echo "沒有這個選項"; continue ;;
  esac
  if [ "$mode" = "2" ] && ! speak_ok "$L"; then
    echo "$N 暫不支援開會/純說，只能「純聽」；開會/純說支援英文、俄文、日文。請按 L 換語言或選 3。"; continue
  fi
  break
done
SPEAK="$L"   # language we speak: en | ru | follow (= whatever the other side spoke last)
if [ "$L" = "auto" ]; then
  if [ "$BOTH" = "1" ]; then SPEAK=follow
  elif [ "$mode" = "2" ]; then
    read -rp "你要說給對方的語言？[Enter=en 英文 / ru 俄文 / ja 日文]: " SPEAK; SPEAK="${SPEAK:-en}"
    [ "$SPEAK" = "ru" ] || [ "$SPEAK" = "ja" ] || SPEAK=en
  fi
fi
if [ "$mode" = "2" ] && { [ "$SPEAK" = "ja" ] || [ "$SPEAK" = "follow" ]; }; then
  if ! MISSING="$(ja_missing)"; then
    if [ "$SPEAK" = "ja" ]; then
      printf '日文語音/翻譯還缺（只需裝一次，裝好再跑）：\n%s\n' "$MISSING"; exit 1
    fi
    printf '日文先不啟用（缺套件，自動模式只做英/俄）。要日文請先裝：\n%s\n' "$MISSING"
    CANDS="en,ru,zh"; FOLLOW_LANGS="en,ru"
  elif ! have_hymt_file; then
    echo "還沒有日文翻譯模型 Hy-MT2（約 1.1 GB），現在連網下載（只有這一次）..."
    tlwu-setup --hymt
  fi
fi
LISTEN_LANG=(--lang "$L"); [ "$L" = "auto" ] && LISTEN_LANG=(--lang auto --lang-candidates "$CANDS")
HY_NOTE="（日文用 OPUS 英文轉接，品質較差；裝 Hy-MT2 可直譯：tlwu-setup --hymt，並在 .venv 裝 llama-cpp-python）"
if [ "$mode" = "1" ]; then echo "聽  $L -> 中文字幕"; else echo "說  中文 -> $SPEAK 語音"; fi

have_pairs() {  # 0 if every OPUS pair is already on disk (no network)
  python - "$@" <<'PY'
import sys
from tinglongwu.models import models_dir
d = models_dir(None) / "opus"
sys.exit(0 if all((d / p).is_dir() for p in sys.argv[1:]) else 1)
PY
}

if [ "$mode" = "1" ]; then
  if [ "$L" = "en" ]; then need=(en-zh); setup=(--to zh)
  elif [ "$L" = "auto" ]; then
    need=(en-zh ru-en); setup=(--langs ru --to zh)
    if printf '%s' ",$CANDS," | grep -q ,ja, && ! hy_ready; then need+=(ja-en); setup=(--langs ru,ja --to zh); echo "$HY_NOTE"; fi
  elif [ "$L" = "ja" ] && hy_ready; then need=(); setup=()   # Hy-MT2: direct ja -> zh, no OPUS pair
  else need=("$L-en" en-zh); setup=(--langs "$L" --to zh)
    [ "$L" = "ja" ] && echo "$HY_NOTE"; fi
  if ! have_pairs "${need[@]}"; then
    echo "還沒有 $L 的翻譯模型，現在連網下載（只有這一次）..."
    tlwu-setup "${setup[@]}"
  fi
  exec tlwu "${LISTEN_LANG[@]}" "${W_LISTEN[@]}"
fi

if [ "$SPEAK" = "follow" ]; then
  # meeting + auto: both directions; Kokoro (en, and ja on its first Japanese sentence) loads now, Vosk ru on first Russian sentence
  if ! have_pairs zh-en en-zh ru-en en-ru; then
    echo "還沒有 英/俄 的翻譯模型，現在連網下載（只有這一次）..."
    tlwu-setup --pair zh-en --pair en-zh --pair ru-en --pair en-ru
  fi
  REV=(python -m tinglongwu.reverse.proto --mic --compute cpu --mt opus --to follow --follow-file "$FOLLOW_FILE"
       --follow-langs "$FOLLOW_LANGS"
       --tts-engine kokoro --tts-python "$KOKORO_PY" --tts-model "$KOKORO_MODEL"
       --vosk-python "$VOSK_PY" --vosk-model "$VOSK_MODEL" "${W_SPEAK[@]}")
  TXT=()
  [ -d "$KOKORO_MODEL" ] || { echo "找不到英文語音模型 $KOKORO_MODEL（Kokoro）"; exit 1; }
  [ -d "$VOSK_MODEL" ] || { echo "找不到俄文語音模型 $VOSK_MODEL（Vosk）"; exit 1; }
elif [ "$SPEAK" = "ja" ]; then
  # zh->ja and the back-translation ja->zh are both direct Hy-MT2 (no OPUS pairs); Kokoro speaks with misaki phonemes
  REV=(python -m tinglongwu.reverse.proto --mic --compute cpu --mt opus --to ja
       --tts-engine kokoro --tts-python "$KOKORO_PY" --tts-model "$KOKORO_MODEL" --speaker "$KOKORO_VOICE" "${W_SPEAK[@]}")
  TXT=(python -m tinglongwu.reverse.proto --mic --no-tts --no-vmic --compute cpu --mt opus --to ja "${W_SPEAK[@]}")
  [ -d "$KOKORO_MODEL" ] || { echo "找不到語音模型 $KOKORO_MODEL（Kokoro）"; exit 1; }
elif [ "$SPEAK" = "en" ]; then
  # zh->en is a direct OPUS pair; en-zh is only for the back-translation shown while confirming
  if ! have_pairs zh-en en-zh; then
    echo "還沒有 中文->英文 的翻譯模型，現在連網下載（只有這一次）..."
    tlwu-setup --pair zh-en --pair en-zh
  fi
  REV=(python -m tinglongwu.reverse.proto --mic --compute cpu --mt opus --to en
       --tts-engine kokoro --tts-python "$KOKORO_PY" --tts-model "$KOKORO_MODEL" --speaker "$KOKORO_VOICE" "${W_SPEAK[@]}")
  TXT=(python -m tinglongwu.reverse.proto --mic --no-tts --no-vmic --compute cpu --mt opus --to en "${W_SPEAK[@]}")
else
  if ! have_pairs zh-en en-ru ru-en en-zh; then
    echo "還沒有 中文->俄文 的翻譯模型，現在連網下載（只有這一次）..."
    tlwu-setup --pair zh-en --pair en-ru --pair ru-en --pair en-zh
  fi
  REV=(python -m tinglongwu.reverse.proto --mic --compute cpu --mt opus
       --tts-engine vosk --tts-python "$VOSK_PY" --tts-model "$VOSK_MODEL" --speaker "$VOICE" "${W_SPEAK[@]}")
  TXT=(python -m tinglongwu.reverse.proto --mic --no-tts --no-vmic --compute cpu --mt opus --to ru "${W_SPEAK[@]}")
fi

if [ "$BOTH" = "1" ]; then
  if [ "$L" = "ru" ] && ! have_pairs ru-en en-zh en-ru; then
    echo "還沒有 ru 的翻譯模型，現在連網下載（只有這一次）..."
    tlwu-setup --langs ru --to zh --pair en-ru
  fi
  read -rp "每句直接送、不確認？[Enter=要確認 / y=直接送]: " ac
  AUTO=(); [ "$ac" = "y" ] && AUTO=(--no-confirm)
  # listen in the background (no keyboard), speak in the foreground; Ctrl+C stops both
  rm -f "$FOLLOW_FILE"
  LF=(); [ "$L" = "auto" ] && LF=(--lang-file "$FOLLOW_FILE")
  tlwu "${LISTEN_LANG[@]}" "${LF[@]}" "${W_LISTEN[@]}" < /dev/null &
  LISTEN_PID=$!
  trap 'kill $LISTEN_PID 2>/dev/null' EXIT INT TERM
    if [ "$IS_MAC" = "1" ]; then MM="BlackHole 16ch"; else MM="tlwu_mic"; fi
  echo "開會模式：[EN]/[RU]/[JA]/[ZH] 是對方的話；你講中文會送進 Meet（麥克風選 $MM）。請戴耳機。"
  "${REV[@]}" "${AUTO[@]}"
  exit
fi
if [ "$IS_MAC" = "1" ]; then MEETMIC="BlackHole 16ch"; else MEETMIC="tlwu_mic"; fi
echo "每句先確認：Enter=送出  x=丟掉  v0-v4=換聲音"
echo "  1) 送進 Meet（Meet 麥克風選 $MEETMIC）"
echo "  2) 送進 Meet，自己耳機也聽得到"
echo "  3) 不進 Meet，直接從耳機/喇叭念出來"
echo "  4) 只看翻譯文字（不發聲）"
read -rp "選 1-4: " out
AUTO=()
if [ "$out" != "4" ]; then
  read -rp "每句直接送、不確認？[Enter=要確認 / y=直接送]: " ac
  [ "$ac" = "y" ] && AUTO=(--no-confirm)
fi
if [ "$IS_MAC" = "1" ] && { [ "$out" = "2" ] || [ "$out" = "3" ]; }; then
  # must be a REAL output (headphones/speakers), never the Multi-Output Device / BlackHole 2ch: those are recorded by the listen side
  echo "你的輸出裝置（不能選「多重輸出裝置」或 BlackHole 2ch）："
  python -m tinglongwu.reverse.proto --list-speakers 2>/dev/null | sed 's/^/    /'
  read -rp "裝置名稱（打一部分即可，例如 AirPods 或 MacBook）: " HP
  [ -n "$HP" ] || { echo "沒有輸入裝置名稱"; exit 1; }
fi
if [ "$IS_MAC" = "1" ]; then
  case "$out" in
    1) exec "${REV[@]}" "${AUTO[@]}" ;;   # proto auto-selects BlackHole 16ch
    2) exec "${REV[@]}" "${AUTO[@]}" --preview-device "$HP" ;;
    3) exec "${REV[@]}" "${AUTO[@]}" --no-vmic --device "$HP" ;;
    4) exec "${TXT[@]}" ;;
    *) echo "沒有這個選項"; exit 1 ;;
  esac
fi
case "$out" in
  1) exec "${REV[@]}" "${AUTO[@]}" ;;
  2) exec "${REV[@]}" "${AUTO[@]}" --preview-device Family ;;
  3) exec "${REV[@]}" "${AUTO[@]}" --device Family ;;
  4) exec "${TXT[@]}" ;;
  *) echo "沒有這個選項"; exit 1 ;;
esac
