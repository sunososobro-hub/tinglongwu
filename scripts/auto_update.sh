#!/usr/bin/env bash
# Self-update used by the menus (sourced, not run; needs tlwu_common.sh loaded first for the messages).
# The ONLY network use besides tlwu-setup/pip: a `git fetch`. The menu calls it at start only when the user turned
# auto-update on, or on demand with U. Never blocks: ~6 s network cap, any failure = one line, continue.
#   tlwu_auto_update   -> sets TLWU_UPDATED=1 when new commits were pulled; TLWU_UPDATE_RESULT=updated|uptodate|skipped|forced_off|nogit
# Skip with TLWU_NO_UPDATE=1. Only acts on a git checkout; local edits that block a fast-forward just produce a warning.
# Portable to macOS bash 3.2 (no `timeout`, no arrays needed).

tlwu_auto_update() {
  TLWU_UPDATED=""; TLWU_UPDATE_RESULT=skipped
  if [ "${TLWU_NO_UPDATE:-}" = "1" ]; then TLWU_UPDATE_RESULT=forced_off; return 0; fi
  command -v git >/dev/null 2>&1 || { TLWU_UPDATE_RESULT=nogit; return 0; }
  [ -d .git ] || { TLWU_UPDATE_RESULT=nogit; return 0; }
  local old up rc pid wd
  old="$(git rev-parse HEAD 2>/dev/null)" || return 0
  # fetch with a watchdog (offline / slow network must not stall the menu)
  GIT_TERMINAL_PROMPT=0 git -c http.lowSpeedLimit=1000 -c http.lowSpeedTime=5 fetch --quiet origin >/dev/null 2>&1 &
  pid=$!
  ( sleep 6; kill "$pid" 2>/dev/null ) >/dev/null 2>&1 &
  wd=$!
  rc=0; wait "$pid" 2>/dev/null || rc=$?
  kill "$wd" 2>/dev/null; wait "$wd" 2>/dev/null || true   # (the menu runs under set -e)
  if [ "$rc" -ne 0 ]; then m M_UPD_SKIP_NET; return 0; fi
  up="$(git rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null)" || up="origin/main"
  if [ "$(git rev-parse "$up" 2>/dev/null)" = "$old" ]; then TLWU_UPDATE_RESULT=uptodate; return 0; fi
  if git merge-base --is-ancestor "$up" HEAD 2>/dev/null; then TLWU_UPDATE_RESULT=uptodate; return 0; fi   # we are ahead / equal
  if ! git merge --ff-only --quiet "$up" >/dev/null 2>&1; then m M_UPD_SKIP_DIVERGED; return 0; fi
  TLWU_UPDATED=1; TLWU_UPDATE_RESULT=updated
  if git diff --name-only "$old" HEAD 2>/dev/null | grep -Eq '^(pyproject\.toml|requirements[^/]*\.txt)$'; then
    m M_UPD_PKG
    if ! python -m pip install -q -e . >/dev/null 2>&1; then
      m M_UPD_PKG_FAIL
    fi
  else
    m M_UPD_OK
  fi
  return 0
}

# U in the menu: update once now (regardless of the auto-update setting); restart the menu if it changed.
tlwu_manual_update() {
  tlwu_auto_update || true
  case "$TLWU_UPDATE_RESULT" in
    forced_off) m M_UPD_FORCED_OFF ;;
    nogit) m M_UPD_NOGIT ;;
    uptodate) m M_UPD_UPTODATE ;;
  esac
  if [ -n "$TLWU_UPDATED" ]; then export TLWU_UPDATED_ONCE=1; exec bash "$TLWU_MENU_SCRIPT" "${TLWU_MENU_ARGS[@]}"; fi
}
