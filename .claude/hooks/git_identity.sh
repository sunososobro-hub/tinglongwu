#!/usr/bin/env bash
# SessionStart hook (#1309, v2 #1454). Writes nothing outside this repo, ever.
#
# Cloud agent sessions (CLAUDE_CODE_REMOTE=true) in the upstream repo only:
# set the owner's commit identity in THIS repo's .git/config (rules section 2).
# A fork's cloud session gets no identity: it commits as whoever owns the fork.
#
# Everyone else (contributors running Claude Code on their own machine): change
# nothing. Only detect leftovers of the old hook (v0.4.6 and older wrote them to
# the global git config) and print the commands for the user to run.
#
# Read-only on the local machine; always exits 0 so it never blocks a session.
trap 'exit 0' EXIT
set +e

OWNER_NAME="sunososobro-hub"
OWNER_EMAIL="243713070+sunososobro-hub@users.noreply.github.com"
UPSTREAM_SUFFIX="sunososobro-hub/tinglongwu"
OLD_HOOKS_NAME=".tlwu-git-hooks"
REPO_DIR="${CLAUDE_PROJECT_DIR:-$PWD}"

if [ "${CLAUDE_CODE_REMOTE:-}" = "true" ]; then
  origin="$(git -C "$REPO_DIR" remote get-url origin 2>/dev/null)"
  origin="${origin%/}"
  origin="${origin%.git}"
  case "$origin" in
    *"/$UPSTREAM_SUFFIX"|*":$UPSTREAM_SUFFIX")
      git -C "$REPO_DIR" config --local user.name "$OWNER_NAME" 2>/dev/null
      git -C "$REPO_DIR" config --local user.email "$OWNER_EMAIL" 2>/dev/null
      ;;
  esac
  exit 0
fi

# Local machine: detect only. Normalise Windows backslashes and trailing slashes,
# then match the old hooks folder by name, so C:/Users/x/.tlwu-git-hooks and
# /home/x/.tlwu-git-hooks/ are both found.
hooks_path="$(git config --global --get core.hooksPath 2>/dev/null)"
hooks_path="${hooks_path//\\//}"
while [ "${hooks_path%/}" != "$hooks_path" ]; do hooks_path="${hooks_path%/}"; done
email="$(git config --global --get user.email 2>/dev/null)"

old_hooks=0
case "$hooks_path" in */"$OLD_HOOKS_NAME"|"$OLD_HOOKS_NAME") old_hooks=1 ;; esac
old_identity=0
[ "$email" = "$OWNER_EMAIL" ] && old_identity=1

if [ "$old_hooks" = 1 ] || [ "$old_identity" = 1 ]; then
  echo "tinglongwu (#1309): an older version of this repo's hook changed your global git config. Nothing was changed now; to undo it, run:"
  if [ "$old_hooks" = 1 ]; then
    echo "  git config --global --unset core.hooksPath"
    echo "  rm -rf ~/$OLD_HOOKS_NAME"
  fi
  echo "  git config --global user.name \"Your Name\""
  echo "  git config --global user.email \"you@example.com\""
  echo "Details: https://github.com/sunososobro-hub/tinglongwu/security/advisories/GHSA-46wr-883c-fp6v"
fi
exit 0
