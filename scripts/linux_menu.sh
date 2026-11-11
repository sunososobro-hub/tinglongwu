#!/usr/bin/env bash
# tinglongWu helper menu (Linux + macOS). Run from the repo:  bash scripts/linux_menu.sh
# Paths can be overridden with env vars before running; WHISPER sets the speech model, e.g.  WHISPER=medium bash scripts/linux_menu.sh
# Menu language follows the system (override: TLWU_UI_LANG=en|ru|zh-TW, or I in the menu); texts live in scripts/i18n/.
# Network is used only for install/setup and update (git); TLWU_NO_UPDATE=1 forces updates off. Everything else is offline.
set -e
cd "$(dirname "$0")/.."
. .venv/bin/activate
IS_MAC=0; [ "$(uname -s)" = "Darwin" ] && IS_MAC=1

TLWU_MENU_SCRIPT="$0"; TLWU_MENU_ARGS=("$@")
. scripts/tlwu_common.sh
tlwu_load_i18n
. scripts/auto_update.sh

# Updating is opt-in: first run asks once (saved in the user config); then every menu start checks GitHub
# (git fetch, ~6 s cap, never blocks) only if you said yes, otherwise press U. Restart once if the update changed this script.
tlwu_consent_autoupdate
if tlwu_autoupdate_on && [ -z "${TLWU_UPDATED_ONCE:-}" ]; then
  tlwu_auto_update || true
  if [ -n "$TLWU_UPDATED" ]; then export TLWU_UPDATED_ONCE=1; exec bash "$0" "$@"; fi
fi
# Blocked-connection reports from the network guard: asks; nothing is sent unless you say y and submit on GitHub.
tlwu_report_check

# Never download silently: every model download from this menu is confirmed first (default = no).
ask_setup() {
  local ans=""
  read -rp "$(mp M_ASK_DOWNLOAD)" ans || ans=""
  case "$ans" in y|Y|yes|YES|Yes) ;; *) m M_DOWNLOAD_DECLINED; exit 1 ;; esac
  tlwu-setup "$@"
}

VOSK_PY="${VOSK_PY:-$HOME/tts-vosk/bin/python}"
VOSK_MODEL="${VOSK_MODEL:-$HOME/vosk-model-tts-ru-0.7-multi}"
WHISPER="${WHISPER:-}"   # empty = program default
W_LISTEN=(); W_SPEAK=()
if [ -n "$WHISPER" ]; then
  W_LISTEN=(--model "$WHISPER"); W_SPEAK=(--whisper "$WHISPER")
  if ! python -c "import sys; from tinglongwu.models import models_dir; sys.exit(0 if (models_dir(None) / 'whisper-$WHISPER').is_dir() else 1)"; then
    m M_NO_WHISPER "$WHISPER"
    ask_setup --whisper "$WHISPER"
  fi
fi
# Developer option, off by default. TLWU_DEV_MONITOR=1: speak modes take the speaker monitor as the mic
# (play a Chinese clip, measure latency without speaking).
if [ "${TLWU_DEV_MONITOR:-}" = "1" ]; then
  W_SPEAK+=(--mic "Monitor of" --allow-monitor)
  echo "[dev] TLWU_DEV_MONITOR=1: speak modes use 'Monitor of' as the microphone input (not your real mic)."
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
lang_name() { local k="M_LN_$1"; if [ -n "${!k:-}" ]; then printf '%s' "${!k}"; else printf '%s' "$1"; fi; }
BOTH=0
speak_ok() { case "$1" in auto|en|ru|ja) return 0;; *) return 1;; esac; }

# Japanese needs Hy-MT2 (llama-cpp-python in .venv + the GGUF) and, for speaking, misaki[ja] in the Kokoro venv.
HYMT_FILE_REL="hymt2-1.8b/Hy-MT2-1.8B-Q4_K_M.gguf"
have_hymt_file() { python -c "import sys; from tinglongwu.models import models_dir; sys.exit(0 if (models_dir(None) / '$HYMT_FILE_REL').is_file() else 1)"; }
have_llama() { python -c "import llama_cpp" 2>/dev/null; }
hy_ready() { have_hymt_file && have_llama; }   # then ja is translated directly (no OPUS pivot)
ja_missing() {  # prints the install commands the Japanese voice/translator still needs; returns 1 if any
  local out=""
  have_llama || out+="  pip install llama-cpp-python --only-binary=:all: --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu   $(mp M_JA_VENV_HINT)"$'\n'
  "$KOKORO_PY" -c "import misaki.ja, fugashi, jaconv, unidic_lite" 2>/dev/null || \
    out+="  $KOKORO_PY -m pip install misaki==0.9.4 pyopenjtalk-plus fugashi unidic-lite mojimoji jaconv addict regex"$'\n'
  [ -z "$out" ] && return 0
  printf '%s' "$out"; return 1
}
while :; do
  N="$(lang_name "$L")"
  NS=""; speak_ok "$L" || NS="$(mp M_UNSUPPORTED "$N")"
  if tlwu_autoupdate_on; then AU="$(mp M_ON)"; else AU="$(mp M_OFF)"; fi
  echo
  m M_HEAD "$N"
  m M_OPT_MEET "$NS"
  if [ "$L" = "auto" ]; then m M_OPT_SPEAK_AUTO; else m M_OPT_SPEAK "$N" "$NS"; fi
  m M_OPT_LISTEN "$N"
  m M_OPT_LANG
  m M_OPT_UPDATE
  m M_OPT_AUTOUPD "$AU"
  m M_OPT_UILANG "$(mp M_LANGNAME)"
  read -rp "> " pick || exit 0
  case "$pick" in
    ""|1) mode=2; BOTH=1 ;;
    2) mode=2; BOTH=0 ;;
    3) mode=1; BOTH=0 ;;
    u|U) tlwu_manual_update || true; continue ;;
    a|A) tlwu_toggle_autoupdate; continue ;;
    i|I) tlwu_pick_ui_lang; continue ;;
    l|L) m M_LANG_HELP
         read -rp "$(mp M_ASK_LANG)" L; L="${L:-auto}"; continue ;;
    *) m M_BAD_OPTION; continue ;;
  esac
  if [ "$mode" = "2" ] && ! speak_ok "$L"; then
    m M_NO_SPEAK "$N"; BOTH=0; continue
  fi
  break
done
SPEAK="$L"   # language we speak: en | ru | follow (= whatever the other side spoke last)
if [ "$L" = "auto" ]; then
  if [ "$BOTH" = "1" ]; then SPEAK=follow
  elif [ "$mode" = "2" ]; then
    read -rp "$(mp M_ASK_SPEAK)" SPEAK; SPEAK="${SPEAK:-en}"
    [ "$SPEAK" = "ru" ] || [ "$SPEAK" = "ja" ] || SPEAK=en
  fi
fi
if [ "$mode" = "2" ] && { [ "$SPEAK" = "ja" ] || [ "$SPEAK" = "follow" ]; }; then
  if ! MISSING="$(ja_missing)"; then
    if [ "$SPEAK" = "ja" ]; then
      mp M_JA_MISSING_FATAL "$MISSING"; exit 1
    fi
    mp M_JA_MISSING_SKIP "$MISSING"
    CANDS="en,ru,zh"; FOLLOW_LANGS="en,ru"
  elif ! have_hymt_file; then
    m M_NO_HYMT
    ask_setup --hymt
  fi
fi
# The Russian voice model has no stated upstream license: opt-in only, never part of a default setup. Asked here
# (default N) only when Russian speech is about to be used; declining = Russian stays subtitles only.
have_vosk() {
  [ -d "$VOSK_MODEL" ] && return 0
  local d; d="$(python -c "from tinglongwu.models import models_dir, VOSK_TTS_RU_DIR; print(models_dir(None) / VOSK_TTS_RU_DIR)" 2>/dev/null)" || return 1
  [ -d "$d" ] && { VOSK_MODEL="$d"; return 0; }
  return 1
}
VOSK_OFF=0
if [ "$mode" = "2" ] && { [ "$SPEAK" = "ru" ] || { [ "$SPEAK" = "follow" ] && printf '%s' ",$FOLLOW_LANGS," | grep -q ,ru,; }; }; then
  if ! have_vosk; then
    m M_VOSK_LICENSE
    vans=""; read -rp "$(mp M_ASK_VOSK)" vans || vans=""
    case "$vans" in y|Y|yes|YES|Yes) tlwu-setup --vosk-tts-ru || true ;; esac
    have_vosk || { m M_VOSK_OPTIN; VOSK_OFF=1; }
  fi
fi
LISTEN_LANG=(--lang "$L"); [ "$L" = "auto" ] && LISTEN_LANG=(--lang auto --lang-candidates "$CANDS")
HY_NOTE="$(mp M_HY_NOTE)"
# Subtitles are never stored unless asked: default N, asked every start (audio is never saved)
SAVE=()
if [ "$mode" = "1" ] || [ "$BOTH" = "1" ]; then
  sv=""; read -rp "$(mp M_ASK_SAVE)" sv || sv=""
  case "$sv" in y|Y|yes|YES|Yes) SAVE=(--save) ;; esac
fi
if [ "$mode" = "1" ]; then m M_BANNER_LISTEN "$L"; else if [ "$SPEAK" = "follow" ]; then m M_BANNER_SPEAK "$(m M_FOLLOW_LABEL)"; else m M_BANNER_SPEAK "$SPEAK"; fi; fi

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
    m M_NO_MODEL_FOR "$L"
    ask_setup "${setup[@]}"
  fi
  exec tlwu "${LISTEN_LANG[@]}" "${W_LISTEN[@]}" "${SAVE[@]}"
fi

if [ "$SPEAK" = "follow" ]; then
  # meeting + auto: both directions; Kokoro (en, and ja on its first Japanese sentence) loads now, Vosk ru on first Russian sentence
  if ! have_pairs zh-en en-zh ru-en en-ru; then
    m M_NO_MODEL_EN_RU
    ask_setup --pair zh-en --pair en-zh --pair ru-en --pair en-ru
  fi
  REV=(python -m tinglongwu.reverse.proto --mic --compute cpu --mt opus --to follow --follow-file "$FOLLOW_FILE"
       --follow-langs "$FOLLOW_LANGS"
       --tts-engine kokoro --tts-python "$KOKORO_PY" --tts-model "$KOKORO_MODEL"
       --vosk-python "$VOSK_PY" --vosk-model "$VOSK_MODEL" "${W_SPEAK[@]}")
  TXT=()
  [ -d "$KOKORO_MODEL" ] || { m M_NO_KOKORO_EN "$KOKORO_MODEL"; exit 1; }
elif [ "$SPEAK" = "ja" ]; then
  # zh->ja and the back-translation ja->zh are both direct Hy-MT2 (no OPUS pairs); Kokoro speaks with misaki phonemes
  REV=(python -m tinglongwu.reverse.proto --mic --compute cpu --mt opus --to ja
       --tts-engine kokoro --tts-python "$KOKORO_PY" --tts-model "$KOKORO_MODEL" --speaker "$KOKORO_VOICE" "${W_SPEAK[@]}")
  TXT=(python -m tinglongwu.reverse.proto --mic --no-tts --no-vmic --compute cpu --mt opus --to ja "${W_SPEAK[@]}")
  [ -d "$KOKORO_MODEL" ] || { m M_NO_KOKORO "$KOKORO_MODEL"; exit 1; }
elif [ "$SPEAK" = "en" ]; then
  # zh->en is a direct OPUS pair; en-zh is only for the back-translation shown while confirming
  if ! have_pairs zh-en en-zh; then
    m M_NO_MODEL_ZH_EN
    ask_setup --pair zh-en --pair en-zh
  fi
  REV=(python -m tinglongwu.reverse.proto --mic --compute cpu --mt opus --to en
       --tts-engine kokoro --tts-python "$KOKORO_PY" --tts-model "$KOKORO_MODEL" --speaker "$KOKORO_VOICE" "${W_SPEAK[@]}")
  TXT=(python -m tinglongwu.reverse.proto --mic --no-tts --no-vmic --compute cpu --mt opus --to en "${W_SPEAK[@]}")
else
  if ! have_pairs zh-en en-ru ru-en en-zh; then
    m M_NO_MODEL_ZH_RU
    ask_setup --pair zh-en --pair en-ru --pair ru-en --pair en-zh
  fi
  REV=(python -m tinglongwu.reverse.proto --mic --compute cpu --mt opus
       --tts-engine vosk --tts-python "$VOSK_PY" --tts-model "$VOSK_MODEL" --speaker "$VOICE" "${W_SPEAK[@]}")
  TXT=(python -m tinglongwu.reverse.proto --mic --no-tts --no-vmic --compute cpu --mt opus --to ru "${W_SPEAK[@]}")
  [ "$VOSK_OFF" = "1" ] && [ "$BOTH" != "1" ] && exec "${TXT[@]}"   # no Russian voice: subtitles only
fi

if [ "$BOTH" = "1" ]; then
  if [ "$L" = "ru" ] && ! have_pairs ru-en en-zh en-ru; then
    m M_NO_MODEL_FOR ru
    ask_setup --langs ru --to zh --pair en-ru
  fi
  read -rp "$(mp M_ASK_AUTOSEND)" ac
  AUTO=(); [ "$ac" = "y" ] && AUTO=(--no-confirm)
  # listen in the background (no keyboard), speak in the foreground; Ctrl+C stops both
  rm -f "$FOLLOW_FILE"
  LF=(); [ "$L" = "auto" ] && LF=(--lang-file "$FOLLOW_FILE")
  tlwu "${LISTEN_LANG[@]}" "${LF[@]}" "${W_LISTEN[@]}" "${SAVE[@]}" < /dev/null &
  LISTEN_PID=$!
  trap 'kill $LISTEN_PID 2>/dev/null' EXIT INT TERM
  if [ "$IS_MAC" = "1" ]; then MM="BlackHole 16ch"; else MM="tlwu_mic"; fi
  m M_MEETING_BANNER "$MM"
  "${REV[@]}" "${AUTO[@]}"
  exit
fi
if [ "$IS_MAC" = "1" ]; then MEETMIC="BlackHole 16ch"; else MEETMIC="tlwu_mic"; fi
m M_CONFIRM_HELP
m M_OUT1 "$MEETMIC"
m M_OUT2
m M_OUT3
m M_OUT4
read -rp "$(mp M_ASK_OUT)" out
AUTO=()
if [ "$out" != "4" ]; then
  read -rp "$(mp M_ASK_AUTOSEND)" ac
  [ "$ac" = "y" ] && AUTO=(--no-confirm)
fi
if [ "$out" = "2" ] || [ "$out" = "3" ]; then
  # (Linux too, #38: never a hardcoded device name) must be a REAL output (headphones/speakers), never the Multi-Output Device / BlackHole 2ch: those are recorded by the listen side
  m M_MAC_OUTDEV
  python -m tinglongwu.reverse.proto --list-speakers 2>/dev/null | sed 's/^/    /'
  read -rp "$(mp M_ASK_DEVICE)" HP
  [ -n "$HP" ] || { m M_NO_DEVICE; exit 1; }
fi
if [ "$IS_MAC" = "1" ]; then
  case "$out" in
    1) exec "${REV[@]}" "${AUTO[@]}" ;;   # proto auto-selects BlackHole 16ch
    2) exec "${REV[@]}" "${AUTO[@]}" --preview-device "$HP" ;;
    3) exec "${REV[@]}" "${AUTO[@]}" --no-vmic --device "$HP" ;;
    4) exec "${TXT[@]}" ;;
    *) m M_BAD_OPTION; exit 1 ;;
  esac
fi
case "$out" in
  1) exec "${REV[@]}" "${AUTO[@]}" ;;
  2) exec "${REV[@]}" "${AUTO[@]}" --preview-device "$HP" ;;
  3) exec "${REV[@]}" "${AUTO[@]}" --device "$HP" ;;
  4) exec "${TXT[@]}" ;;
  *) m M_BAD_OPTION; exit 1 ;;
esac
