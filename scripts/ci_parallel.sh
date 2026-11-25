#!/usr/bin/env bash
# CI (#1857): run labelled shell commands at the same time and fail when ANY of them fails.
#   bash scripts/ci_parallel.sh <label> '<command>' [<label> '<command>' ...]
# Each command runs in `bash -c` with its own TMPDIR (so two pytest passes never share a temp file or basetemp);
# its output is streamed live with a `[label] ` prefix. Exit code: 0 when every command exited 0, else 1.
set -u
if [ $# -eq 0 ] || [ $(( $# % 2 )) -ne 0 ]; then
  echo "usage: ci_parallel.sh <label> '<command>' [<label> '<command>' ...]" >&2
  exit 2
fi
DIR="$(mktemp -d "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/ci_parallel.XXXXXX")"
LABELS=""
while [ $# -gt 0 ]; do
  label="$1"; cmd="$2"; shift 2
  mkdir -p "$DIR/$label.tmp"
  LABELS="$LABELS $label"
  ( ( TMPDIR="$DIR/$label.tmp" bash -c "$cmd" 2>&1; echo $? > "$DIR/$label.rc" ) | sed -u "s/^/[$label] /" ) &
done
wait
status=0
for label in $LABELS; do
  rc="$(cat "$DIR/$label.rc" 2>/dev/null || echo 127)"
  echo "[ci_parallel] $label exit $rc"
  [ "$rc" = "0" ] || status=1
done
exit $status
