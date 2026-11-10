#!/usr/bin/env bash
# Quiet self-update used by the menus (sourced, not run). Never blocks: ~6 s network cap, any failure = one line, continue.
#   tlwu_auto_update   -> sets TLWU_UPDATED=1 when new commits were pulled
# Skip with TLWU_NO_UPDATE=1. Only acts on a git checkout; local edits that block a fast-forward just produce a warning.
# Portable to macOS bash 3.2 (no `timeout`, no arrays needed).

tlwu_auto_update() {
  TLWU_UPDATED=""
  [ "${TLWU_NO_UPDATE:-}" = "1" ] && return 0
  command -v git >/dev/null 2>&1 || return 0
  [ -d .git ] || return 0
  local old up rc pid wd
  old="$(git rev-parse HEAD 2>/dev/null)" || return 0
  # fetch with a watchdog (offline / slow network must not stall the menu)
  GIT_TERMINAL_PROMPT=0 git -c http.lowSpeedLimit=1000 -c http.lowSpeedTime=5 fetch --quiet origin >/dev/null 2>&1 &
  pid=$!
  ( sleep 6; kill "$pid" 2>/dev/null ) >/dev/null 2>&1 &
  wd=$!
  rc=0; wait "$pid" 2>/dev/null || rc=$?
  kill "$wd" 2>/dev/null; wait "$wd" 2>/dev/null
  if [ "$rc" -ne 0 ]; then echo "（自動更新略過：連不上網路，使用目前版本）"; return 0; fi
  up="$(git rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null)" || up="origin/main"
  [ "$(git rev-parse "$up" 2>/dev/null)" = "$old" ] && return 0
  if git merge-base --is-ancestor "$up" HEAD 2>/dev/null; then return 0; fi   # we are ahead / equal
  if ! git merge --ff-only --quiet "$up" >/dev/null 2>&1; then
    echo "（自動更新略過：本機有改動或分支不同，無法快轉合併；使用目前版本）"; return 0
  fi
  TLWU_UPDATED=1
  if git diff --name-only "$old" HEAD 2>/dev/null | grep -Eq '^(pyproject\.toml|requirements[^/]*\.txt)$'; then
    echo "已更新到最新版，正在更新套件（約一分鐘）..."
    if ! python -m pip install -q -e . >/dev/null 2>&1; then
      echo "（套件更新失敗，請稍後重跑安裝腳本修復；先用目前環境繼續）"
    fi
  else
    echo "已更新到最新版。"
  fi
  return 0
}
