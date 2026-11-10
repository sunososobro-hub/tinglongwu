#!/usr/bin/env bash
# CI: run the test suite in an empty Linux network namespace (only loopback). Needs sudo + util-linux (ubuntu runners).
# The in-process guard (tinglongwu.offline) is on too, so a library that phones home is blocked, logged and fails the test.
set -eu
U="$(id -u)"; G="$(id -g)"
exec sudo unshare -n -- sh -c '
  ip link set lo up
  if curl -sS -m 5 -o /dev/null https://example.com 2>/dev/null; then echo "network is NOT disabled"; exit 1; fi
  echo "network disabled: ok"
  exec setpriv --reuid='"$U"' --regid='"$G"' --init-groups env "PATH='"$PATH"'" "HOME='"$HOME"'" "$@"
' sh "$@"
