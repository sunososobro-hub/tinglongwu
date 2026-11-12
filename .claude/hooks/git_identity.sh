#!/usr/bin/env bash
# SessionStart hook: make every commit carry the owner's identity and a
# Co-Authored-By trailer. Idempotent; always exits 0 so it never blocks a session.
trap 'exit 0' EXIT
set +e

git config --global user.name "sunososobro-hub" 2>/dev/null
git config --global user.email "243713070+sunososobro-hub@users.noreply.github.com" 2>/dev/null

HOOKS_DIR="${HOME:-/tmp}/.tlwu-git-hooks"
mkdir -p "$HOOKS_DIR" 2>/dev/null

cat > "$HOOKS_DIR/commit-msg" <<'HOOK'
#!/bin/sh
# Append the Co-Authored-By trailer when it is missing.
f="$1"
[ -f "$f" ] || exit 0
line='Co-Authored-By: Claude <noreply@anthropic.com>'
grep -qxF "$line" "$f" 2>/dev/null && exit 0
printf '\n%s\n' "$line" >> "$f"
exit 0
HOOK
chmod +x "$HOOKS_DIR/commit-msg" 2>/dev/null

git config --global core.hooksPath "$HOOKS_DIR" 2>/dev/null
exit 0
