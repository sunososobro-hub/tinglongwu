#!/usr/bin/env bash
# Shared helpers for the bash menu (Linux + macOS), sourced by linux_menu.sh. bash 3.2 compatible.
#   UI language : env TLWU_UI_LANG > config ui_lang > system (macOS AppleLanguages / LC_ALL,LC_MESSAGES,LANG) > en
#   messages    : scripts/i18n/<lang>.sh (en.sh is the base; add a language = add a file + a line in tlwu_norm_lang)
#   config file : KEY=VALUE lines (auto_update=0|1, ui_lang=...) in the user config dir (same file the Python side reads)

TLWU_I18N_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/i18n"

tlwu_cfg_dir() {
  if [ -n "${TLWU_CONFIG_DIR:-}" ]; then printf '%s' "$TLWU_CONFIG_DIR"
  elif [ "$(uname -s)" = "Darwin" ]; then printf '%s' "$HOME/Library/Application Support/tinglongwu"
  else printf '%s' "${XDG_CONFIG_HOME:-$HOME/.config}/tinglongwu"; fi
}
tlwu_cfg_get() {  # tlwu_cfg_get KEY -> value (empty if unset)
  local f; f="$(tlwu_cfg_dir)/config"
  [ -f "$f" ] || return 0
  sed -n "s/^$1=//p" "$f" | tail -n 1 | tr -d '\r'
}
tlwu_cfg_set() {  # tlwu_cfg_set KEY VALUE
  local d f tmp; d="$(tlwu_cfg_dir)"; f="$d/config"
  mkdir -p "$d" 2>/dev/null || return 0
  tmp="$f.tmp.$$"
  { [ -f "$f" ] && grep -v "^$1=" "$f"; printf '%s=%s\n' "$1" "$2"; } > "$tmp" 2>/dev/null && mv "$tmp" "$f"
  return 0
}

tlwu_norm_lang() {  # locale tag (ru_RU.UTF-8, zh-Hant-TW, en-US ...) -> en | ru | zh-TW | ja
  local t; t="$(printf '%s' "$1" | tr 'A-Z_' 'a-z-')"
  case "$t" in
    ru*) echo ru ;;
    ja*) echo ja ;;
    zh*) echo zh-TW ;;   # zh-CN also gets the zh-TW text (no separate zh-CN file yet)
    *) echo en ;;
  esac
}
tlwu_sys_lang() {
  local t=""
  if [ "$(uname -s)" = "Darwin" ]; then
    t="$(defaults read -g AppleLanguages 2>/dev/null | sed -n '2p' | tr -d ' ",')"
  fi
  if [ -z "$t" ]; then t="${LC_ALL:-}"; [ -n "$t" ] || t="${LC_MESSAGES:-}"; [ -n "$t" ] || t="${LANG:-}"; fi
  tlwu_norm_lang "$t"
}
tlwu_resolve_lang() {
  local c="${TLWU_UI_LANG:-}"
  [ "$c" = "auto" ] && c=""
  [ -n "$c" ] || c="$(tlwu_cfg_get ui_lang)"
  [ "$c" = "auto" ] && c=""
  if [ -n "$c" ]; then tlwu_norm_lang "$c"; else tlwu_sys_lang; fi
}
tlwu_load_i18n() {
  TLWU_UI="$(tlwu_resolve_lang)"
  # shellcheck disable=SC1090
  . "$TLWU_I18N_DIR/en.sh"
  # shellcheck disable=SC1090
  [ "$TLWU_UI" = "en" ] || { [ -f "$TLWU_I18N_DIR/$TLWU_UI.sh" ] && . "$TLWU_I18N_DIR/$TLWU_UI.sh"; }
  export TLWU_UI_LANG="$TLWU_UI"   # the Python side (guard messages, netreport) follows the menu
  return 0
}
# m KEY args... : print message + newline;  mp KEY args... : no newline (read prompts, $(mp KEY))
m()  { local _f="${!1}"; shift; printf -- "$_f\n" "$@"; }
mp() { local _f="${!1}"; shift; printf -- "$_f" "$@"; }

tlwu_autoupdate_on() { [ "${TLWU_NO_UPDATE:-}" != "1" ] && [ "$(tlwu_cfg_get auto_update)" = "1" ]; }

# First run (no config yet): ask once. No answer possible (stdin closed) = stay off, nothing saved.
tlwu_consent_autoupdate() {
  [ "${TLWU_NO_UPDATE:-}" = "1" ] && return 0
  [ -n "$(tlwu_cfg_get auto_update)" ] && return 0
  local ans
  if read -rp "$(mp M_ASK_AUTOUPD)" ans; then
    case "$ans" in
      y|Y|yes|YES|Yes) tlwu_cfg_set auto_update 1; m M_AUTOUPD_SET_ON ;;
      *) tlwu_cfg_set auto_update 0; m M_AUTOUPD_SET_OFF ;;
    esac
  else echo; fi
  return 0
}
tlwu_toggle_autoupdate() {
  if [ "${TLWU_NO_UPDATE:-}" = "1" ]; then m M_UPD_FORCED_OFF; return 0; fi
  if tlwu_autoupdate_on; then tlwu_cfg_set auto_update 0; m M_AUTOUPD_SET_OFF
  else tlwu_cfg_set auto_update 1; m M_AUTOUPD_SET_ON; fi
}
tlwu_pick_ui_lang() {
  local c
  read -rp "$(mp M_ASK_UILANG)" c || return 0
  c="${c:-auto}"
  case "$c" in auto|en|ru|ja|zh-TW|zh-tw|zh) ;; *) c="$(tlwu_norm_lang "$c")" ;; esac
  tlwu_cfg_set ui_lang "$c"
  unset TLWU_UI_LANG   # let the saved choice win over an earlier export
  tlwu_load_i18n
}

# Network-guard report (opt-in): count + hosts, ask, y = open a prefilled GitHub issue after showing it, either answer marks them handled.
tlwu_report_check() {
  local line ans
  line="$(python -m tinglongwu.netreport status 2>/dev/null)" || return 0
  echo "$line"
  if read -rp "$(mp M_ASK_REPORT)" ans; then
    case "$ans" in
      y|Y|yes|YES|Yes) python -m tinglongwu.netreport send || true ;;
      *) python -m tinglongwu.netreport dismiss >/dev/null 2>&1 || true ;;
    esac
  else echo; fi
  return 0
}

tlwu_install_selftest() {  # tlwu_install_selftest TLWU_SETUP [args...]: last installer step (#99), offline
  # Runs `tlwu-setup --selftest args` (loads the models, translates one test sentence). Returns 1 only when the
  # self-test failed (the installer then warns; nothing is deleted); a tlwu without --selftest (exit 3) is skipped.
  local bin="$1" rc; shift
  printf '    %s\n' "自我測試：載入模型並翻譯一句測試句 / self-test: loading the models, translating one test sentence"
  "$bin" --selftest "$@"; rc=$?
  case "$rc" in
    0) printf '    %s\n' "自我測試通過 / self-test passed" ;;
    3) printf '    %s\n' "此版本尚無自我測試，略過 / this version has no self-test yet: skipped" ;;
    *) return 1 ;;
  esac
  return 0
}

# #420: install llama-cpp-python from a requirements file; if the index wheel fails (e.g. the 0.3.36 arm64 wheel on the
# CPU index is a corrupt zip), build the PyPI sdist of the same pinned version instead.
# #1341: that sdist build is the slowest part of a Mac install (~180 s of ~320 s to the first subtitle). Without ninja
# pip's build backend drives CMake with make, which compiles one file at a time unless CMAKE_BUILD_PARALLEL_LEVEL is
# set, so the build gets every core (a value the user already exported wins).
tlwu_ncpu() {  # online CPU cores (macOS + Linux); TLWU_NCPU_FALLBACK when unknown
  local n; n="$(getconf _NPROCESSORS_ONLN 2>/dev/null)"
  case "$n" in ''|*[!0-9]*|0) n="$TLWU_NCPU_FALLBACK" ;; esac
  printf '%s' "$n"
}
TLWU_NCPU_FALLBACK=2
# #1341 (Opus step 1): the sdist fallback builds only the library; the llava, tests, examples and server targets are not
# used by Hy-MT2 and cost build time. A CMAKE_ARGS the user already exported wins.
TLWU_LLAMA_CMAKE_ARGS="-DLLAVA_BUILD=OFF -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF -DLLAMA_BUILD_SERVER=OFF"
# #1356: Apple Silicon takes the wheel this project builds in CI (.github/workflows/llama-wheel-mac.yml) and publishes
# on the pre-release <TLWU_LLAMA_WHEEL_TAG_PREFIX><version>; the version is the pin in the requirements file, nothing
# else names it. Any failure (no release, download, checksum, pip, import) leaves the old path below to run.
TLWU_LLAMA_PKG="llama-cpp-python"
TLWU_LLAMA_WHEEL_BASE_DEFAULT="https://github.com/sunososobro-hub/tinglongwu/releases/download"
TLWU_LLAMA_WHEEL_TAG_PREFIX="wheels-llama-cpp-python-"
TLWU_LLAMA_WHEEL_SUMS="SHA256SUMS.txt"
TLWU_LLAMA_WHEEL_PLATFORM_MAC_ARM64="macosx_arm64"
TLWU_LLAMA_SUMS_TIMEOUT_S=30     # curl --max-time for the checksum file (under 1 kB)
TLWU_LLAMA_WHEEL_TIMEOUT_S=120   # curl --max-time for the wheel; slower than that, the old path runs
TLWU_SHA256_HEX_LEN=64
tlwu_llama_pin() {  # tlwu_llama_pin REQUIREMENTS_FILE -> llama-cpp-python==X.Y.Z (empty when the file has no pin)
  grep -E "^$TLWU_LLAMA_PKG==" "$1" 2>/dev/null | head -n1 | tr -d '[:space:]'
}
tlwu_llama_version() {  # tlwu_llama_version REQUIREMENTS_FILE -> X.Y.Z
  local pin; pin="$(tlwu_llama_pin "$1")"
  printf '%s' "${pin#*==}"
}
tlwu_llama_wheel_tag() {  # tlwu_llama_wheel_tag REQUIREMENTS_FILE -> release tag that carries the wheel of that pin
  local v; v="$(tlwu_llama_version "$1")"
  [ -n "$v" ] || return 1
  printf '%s%s' "$TLWU_LLAMA_WHEEL_TAG_PREFIX" "$v"
}
tlwu_llama_wheel_platform() {  # platform with a project-built wheel, or empty (TLWU_LLAMA_WHEEL_PLATFORM: tests)
  if [ -n "${TLWU_LLAMA_WHEEL_PLATFORM+x}" ]; then printf '%s' "$TLWU_LLAMA_WHEEL_PLATFORM"; return 0; fi
  [ "$(uname -s)" = "Darwin" ] && [ "$(uname -m)" = "arm64" ] && printf '%s' "$TLWU_LLAMA_WHEEL_PLATFORM_MAC_ARM64"
  return 0
}
tlwu_llama_wheel_glob() {  # tlwu_llama_wheel_glob VERSION PLATFORM -> case pattern of that platform's wheel file name
  case "$2" in
    "$TLWU_LLAMA_WHEEL_PLATFORM_MAC_ARM64") printf 'llama_cpp_python-%s-*-macosx_*_arm64.whl' "$1" ;;
    *) return 1 ;;
  esac
}
tlwu_sha256() {  # tlwu_sha256 FILE -> lowercase hex digest (macOS: shasum; Linux: sha256sum)
  if command -v shasum >/dev/null 2>&1; then shasum -a 256 "$1" | cut -d' ' -f1
  else sha256sum "$1" | cut -d' ' -f1; fi
}
tlwu_install_llama_prebuilt() {  # tlwu_install_llama_prebuilt PYTHON REQUIREMENTS_FILE -> 0 = installed and importable
  local py="$1" req="$2" plat ver tag glob base tmp line want name got rc=1
  [ "${TLWU_LLAMA_NO_PREBUILT:-}" = "1" ] && return 1
  plat="$(tlwu_llama_wheel_platform)"; [ -n "$plat" ] || return 1
  ver="$(tlwu_llama_version "$req")"; tag="$(tlwu_llama_wheel_tag "$req")" || return 1
  glob="$(tlwu_llama_wheel_glob "$ver" "$plat")" || return 1
  base="${TLWU_LLAMA_WHEEL_BASE:-$TLWU_LLAMA_WHEEL_BASE_DEFAULT}/$tag"
  tmp="$(mktemp -d "${TMPDIR:-/tmp}/tlwu_llama_wheel.XXXXXX")" || return 1
  while :; do   # one pass; `break` = give up on the prebuilt wheel
    curl -fsSL --retry 2 --max-time "$TLWU_LLAMA_SUMS_TIMEOUT_S" -o "$tmp/$TLWU_LLAMA_WHEEL_SUMS" \
      "$base/$TLWU_LLAMA_WHEEL_SUMS" 2>/dev/null || { echo "    prebuilt wheel: no checksum file at $base"; break; }
    want=""; name=""
    while read -r want name; do   # "<sha256>  <file name>" lines; take this platform's wheel
      name="${name#\*}"
      # shellcheck disable=SC2254
      case "$name" in */*|*..*) ;; $glob) break ;; esac
      want=""; name=""
    done < "$tmp/$TLWU_LLAMA_WHEEL_SUMS"
    case "$want" in *[!0-9a-f]*) want="" ;; esac
    [ "${#want}" = "$TLWU_SHA256_HEX_LEN" ] && [ -n "$name" ] \
      || { echo "    prebuilt wheel: $TLWU_LLAMA_WHEEL_SUMS lists no $plat wheel of $ver"; break; }
    curl -fsSL --retry 2 --max-time "$TLWU_LLAMA_WHEEL_TIMEOUT_S" -o "$tmp/$name" "$base/$name" 2>/dev/null \
      || { echo "    prebuilt wheel: download of $name failed"; break; }
    got="$(tlwu_sha256 "$tmp/$name")"
    [ "$got" = "$want" ] || { echo "    prebuilt wheel: SHA256 mismatch for $name (got ${got:-none}), not used"; break; }
    "$py" -m pip install "$tmp/$name" || { echo "    prebuilt wheel: pip could not install $name"; break; }
    if "$py" -c "import llama_cpp"; then rc=0; echo "    prebuilt wheel installed: $name (SHA256 ok)"
    else
      echo "    prebuilt wheel: installed but does not import; removing it"
      "$py" -m pip uninstall -y "$TLWU_LLAMA_PKG" >/dev/null 2>&1
    fi
    break
  done
  rm -rf "$tmp"
  return "$rc"
}
tlwu_install_llama() {  # tlwu_install_llama PYTHON REQUIREMENTS_FILE
  local py="$1" req="$2" pin
  tlwu_install_llama_prebuilt "$py" "$req" && return 0
  "$py" -m pip install -r "$req" && return 0
  pin="$(tlwu_llama_pin "$req")"
  [ -n "$pin" ] || return 1
  echo "    index wheel failed; building $pin from the PyPI sdist (a few minutes) ..."
  CMAKE_ARGS="${CMAKE_ARGS:-$TLWU_LLAMA_CMAKE_ARGS}" \
  CMAKE_BUILD_PARALLEL_LEVEL="${CMAKE_BUILD_PARALLEL_LEVEL:-$(tlwu_ncpu)}" \
    "$py" -m pip install --no-binary "$TLWU_LLAMA_PKG" "$pin"
}
# #1341: the same install in the background, so the installer can download the models (network) while the engine
# compiles (CPU). Output goes to LOG; tlwu_wait_llama LOG waits, prints that output and returns the install's exit code.
TLWU_LLAMA_PID=""
tlwu_install_llama_bg() {  # tlwu_install_llama_bg PYTHON REQUIREMENTS_FILE LOG
  ( t0=$SECONDS; tlwu_install_llama "$1" "$2"; rc=$?
    echo "    llama-cpp-python: $((SECONDS - t0)) s (exit $rc)"; exit "$rc" ) >"$3" 2>&1 </dev/null &
  TLWU_LLAMA_PID=$!
}
tlwu_wait_llama() {  # tlwu_wait_llama LOG
  local rc=0
  [ -n "$TLWU_LLAMA_PID" ] || return 1
  wait "$TLWU_LLAMA_PID" || rc=$?
  TLWU_LLAMA_PID=""
  cat "$1" 2>/dev/null
  rm -f "$1"
  return "$rc"
}

# #371: speak mode sends every sentence directly (no confirm question). Confirm mode: `--confirm` argument or TLWU_CONFIRM=1.
# tlwu_confirm_mode "$@" -> 0 if confirm mode was asked for
tlwu_confirm_mode() {
  [ "${TLWU_CONFIRM:-}" = "1" ] && return 0
  local a; for a in "$@"; do [ "$a" = "--confirm" ] && return 0; done
  return 1
}

# #40: meeting-mode start lines: banner, where the translated voice goes,
# and how sending works (confirm mode: y+Enter=send, Enter=discard; otherwise direct send and how to switch).
# tlwu_meeting_banner <virtual mic> <confirm: 1|0>
tlwu_meeting_banner() {
  m M_MEETING_BANNER "$1"
  m M_MEET_ROUTE "$1"
  if [ "$2" = "1" ]; then m M_CONFIRM_HELP; else m M_MEET_DIRECT_HINT; fi
}
