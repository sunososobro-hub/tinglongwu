#!/usr/bin/env bash
# Run a command (default: tlwu) with the network cut at OS level, as the current user (not root).
# Usage: scripts/linux_offline_run.sh [--dry-run] [--] [command ...]
#   1. sudo unshare -n + setpriv (works where AppArmor blocks unprivileged `unshare -rn`, Ubuntu 23.10+)
#   2. fallback: firejail --noprofile --net=none
#   3. neither: print instructions and exit 3
#   Arguments starting with "-" are tlwu options: `linux_offline_run.sh --file talk.wav` runs
#   `<python> -m tinglongwu --file talk.wav`.
# --dry-run prints the final command instead of running it.
set -u

dry=0
if [ "${1:-}" = "--dry-run" ]; then dry=1; shift; fi
if [ "${1:-}" = "--" ]; then shift; fi

# sudo's secure_path drops the venv from PATH, so the command is resolved to an absolute path here.
py_bin() {
    case "$0" in */*) d="${0%/*}" ;; *) d=. ;; esac
    here="$(cd "$d/.." && pwd)"
    for c in ${VIRTUAL_ENV:+"$VIRTUAL_ENV/bin/python"} "$here/.venv/bin/python"; do
        if [ -x "$c" ]; then printf '%s\n' "$c"; return; fi
    done
    command -v python3 || command -v python || printf 'python3\n'
}
case "${1:-}" in
    ""|-*) set -- "$(py_bin)" -m tinglongwu "$@" ;;
    */*) ;;
    *) if p="$(command -v "$1")" && [ "${p#/}" != "$p" ]; then shift; set -- "$p" "$@"; fi ;;
esac

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
