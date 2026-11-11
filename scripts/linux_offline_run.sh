#!/usr/bin/env bash
# Run a command (default: tlwu) with the network cut at OS level, as the current user (not root).
# Usage: scripts/linux_offline_run.sh [--dry-run] [--] [command ...]
#   1. sudo unshare -n + setpriv (works where AppArmor blocks unprivileged `unshare -rn`, Ubuntu 23.10+)
#   2. fallback: firejail --noprofile --net=none
#   3. neither: print instructions and exit 3
# --dry-run prints the final command instead of running it.
set -u

dry=0
if [ "${1:-}" = "--dry-run" ]; then dry=1; shift; fi
if [ "${1:-}" = "--" ]; then shift; fi
if [ "$#" -eq 0 ]; then set -- python -m tinglongwu; fi

uid="${TLWU_UID:-$(id -u)}"
gid="${TLWU_GID:-$(id -g)}"
home="${HOME:-/tmp}"
xdg="${XDG_RUNTIME_DIR:-/run/user/$uid}"

have() { command -v "$1" >/dev/null 2>&1; }

if have sudo && have unshare && have setpriv; then
    cmd=(sudo unshare -n -- sh -c 'ip link set lo up 2>/dev/null; exec "$@"' sh
         setpriv "--reuid=$uid" "--regid=$gid" --init-groups
         env "HOME=$home" "XDG_RUNTIME_DIR=$xdg" TLWU_NO_UPDATE=1 "$@")
elif have firejail; then
    cmd=(firejail --noprofile --net=none env TLWU_NO_UPDATE=1 "$@")
else
    printf '%s\n' "No way to cut the network found. Install one of:" \
        "  - sudo + util-linux (unshare, setpriv):  sudo apt install util-linux" \
        "  - firejail:                              sudo apt install firejail" >&2
    exit 3
fi

if [ "$dry" -eq 1 ]; then
    printf '%q ' "${cmd[@]}"; printf '\n'
    exit 0
fi
exec "${cmd[@]}"
