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

tlwu_norm_lang() {  # locale tag (ru_RU.UTF-8, zh-Hant-TW, en-US ...) -> en | ru | zh-TW
  local t; t="$(printf '%s' "$1" | tr 'A-Z_' 'a-z-')"
  case "$t" in
    ru*) echo ru ;;
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
  case "$c" in auto|en|ru|zh-TW|zh-tw|zh) ;; *) c="$(tlwu_norm_lang "$c")" ;; esac
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
tlwu_install_llama() {  # tlwu_install_llama PYTHON REQUIREMENTS_FILE
  local py="$1" req="$2" pin
  "$py" -m pip install -r "$req" && return 0
  pin="$(grep -E '^llama-cpp-python==' "$req" | head -n1 | tr -d '[:space:]')"
  [ -n "$pin" ] || return 1
  echo "    index wheel failed; building $pin from the PyPI sdist (a few minutes) ..."
  "$py" -m pip install --no-binary llama-cpp-python "$pin"
}
