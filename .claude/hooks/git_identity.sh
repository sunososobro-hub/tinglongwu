#!/usr/bin/env bash
# SessionStart hook (#1309).
#
# Cloud agent sessions (CLAUDE_CODE_REMOTE=true): set the owner's commit identity
# in THIS repo's .git/config only (rules section 2), never in the global config.
#
# Everyone else (contributors running Claude Code on their own machine): change
# nothing. Only undo what older versions of this hook wrote to the global config:
# they set user.name/user.email and core.hooksPath=~/.tlwu-git-hooks for every
# repo on the machine. The ~/.tlwu-git-hooks path proves the old hook did it, so
# only then are those global values removed.
#
# Idempotent; always exits 0 so it never blocks a session.
trap 'exit 0' EXIT
set +e

OWNER_NAME="sunososobro-hub"
OWNER_EMAIL="243713070+sunososobro-hub@users.noreply.github.com"
OLD_HOOKS_DIR="${HOME:-/tmp}/.tlwu-git-hooks"
REPO_DIR="${CLAUDE_PROJECT_DIR:-$PWD}"

if [ "${CLAUDE_CODE_REMOTE:-}" = "true" ]; then
  git -C "$REPO_DIR" config --local user.name "$OWNER_NAME" 2>/dev/null
  git -C "$REPO_DIR" config --local user.email "$OWNER_EMAIL" 2>/dev/null
  exit 0
fi

# Local machine: clean up the old hook's global changes, if present.
if [ "$(git config --global --get core.hooksPath 2>/dev/null)" = "$OLD_HOOKS_DIR" ]; then
  git config --global --unset core.hooksPath 2>/dev/null
  rm -rf "$OLD_HOOKS_DIR" 2>/dev/null
  if [ "$(git config --global --get user.email 2>/dev/null)" = "$OWNER_EMAIL" ]; then
    git config --global --unset user.email 2>/dev/null
    if [ "$(git config --global --get user.name 2>/dev/null)" = "$OWNER_NAME" ]; then
      git config --global --unset user.name 2>/dev/null
    fi
  fi
  echo "tinglongwu: removed the global git settings an older version of this repo's hook wrote (#1309). Set your own identity again with: git config --global user.name \"Your Name\" && git config --global user.email you@example.com"
fi
exit 0
