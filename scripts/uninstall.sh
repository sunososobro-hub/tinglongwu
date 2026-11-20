#!/usr/bin/env bash
# tinglongWu uninstaller for Linux and macOS (#476). Removes what scripts/install.sh / mac_install.sh created:
# the app folder (TLWU_DIR, default ~/tinglongwu, incl. its .venv), the Kokoro voice venv (~/tts-kokoro), the
# tlwu-menu launcher and the PATH line mac_install.sh added. Downloaded models (~/models) are KEPT unless --purge.
# BlackHole / other system audio drivers are never touched; the hint printed at the end says how to remove them.
#   bash scripts/uninstall.sh [--purge] [--yes]
# Override: TLWU_DIR, KOKORO_VENV, KOKORO_DIR, TLWU_MODELS (models folder, default ~/models), TLWU_YES=1.
set -u
DIR="${TLWU_DIR:-$HOME/tinglongwu}"
KOKORO_VENV="${KOKORO_VENV:-$HOME/tts-kokoro}"
MODELS="${TLWU_MODELS:-$HOME/models}"
KOKORO_DIR="${KOKORO_DIR:-$MODELS/kokoro}"
BIN_DIR="${TLWU_BIN_DIR:-$HOME/.local/bin}"
YES="${TLWU_YES:-}"; PURGE=""
for a in "$@"; do case "$a" in --yes) YES=1 ;; --purge) PURGE=1 ;; esac; done

say() { printf '==> %s\n' "$*"; }
FAILED=""
rm_path() {
  if [ -e "$1" ] || [ -L "$1" ]; then
    if rm -rf -- "$1" && [ ! -e "$1" ]; then say "removed $1"; else echo "[x] could not remove $1" >&2; FAILED=1; fi
  fi
}
# never recursively delete empty / root / home targets
safe_target() {
  local p="${1%/}"
  [ -n "$p" ] || return 1
  case "$p" in /|.|..|"$HOME"|"${HOME%/}") return 1 ;; esac
  [ "$(cd "$p" 2>/dev/null && pwd -P)" = "$(cd "$HOME" 2>/dev/null && pwd -P)" ] && return 1
  [ "$(cd "$p" 2>/dev/null && pwd -P)" = "/" ] && return 1
  return 0
}
for t in "$DIR" "$KOKORO_VENV" "$MODELS"; do
  safe_target "$t" || { echo "[x] refusing to remove '$t'" >&2; exit 1; }
done
# models kept by default must not live inside a folder that is about to be removed (compare canonical paths)
if [ -z "$PURGE" ] && [ -d "$MODELS" ]; then
  m="$(cd "$MODELS" && pwd -P)"
  for t in "$DIR" "$KOKORO_VENV"; do
    [ -d "$t" ] || continue
    c="$(cd "$t" && pwd -P)"
    case "$m/" in "$c/"*) echo "[x] models folder $MODELS is inside $t; move it or use --purge" >&2; exit 1 ;; esac
  done
fi
# the app folder must be a tinglongWu checkout (app-specific marker), not any git/python project
if [ -d "$DIR" ] && [ ! -f "$DIR/scripts/linux_menu.sh" ] && [ ! -d "$DIR/tinglongwu" ]; then
  echo "[x] $DIR is not a tinglongWu folder; set TLWU_DIR" >&2; exit 1
fi

# #1456: remove only what tinglongWu created in the models folder: the entries named in models/manifest.json
# (key -> dir; opus-X -> opus/X; whisper-cpp-* -> whisper-cpp/), the manifest itself and the Kokoro folder.
# Foreign files stay; the folder itself is removed only if it is empty afterwards.
purge_models() {
  local keys k
  if [ -f "$MODELS/manifest.json" ]; then
    # one path per line, relative to the models folder; whisper-cpp entries list only their own files
    if ! command -v python3 >/dev/null 2>&1 || ! keys="$(python3 -c '
import json, sys
m = json.load(open(sys.argv[1]))
for k, v in m.items():
    if k.startswith("whisper-cpp-"):
        for f in ((v or {}).get("files") or {}):
            print("whisper-cpp/" + f)
    elif k.startswith("opus-"):
        print("opus/" + k[5:])
    else:
        print(k)
' "$MODELS/manifest.json" 2>/dev/null)"; then
      echo "[x] cannot read $MODELS/manifest.json (python3 missing or manifest invalid); kept $MODELS" >&2
      FAILED=1; return
    fi
    while IFS= read -r k; do
      case "$k" in ""|/*|*..*) continue ;; esac
      # never delete through a symlink: a linked component could point at another tool's folder
      local p="$MODELS" c rest="$k" linked=""
      while [ -n "$rest" ]; do
        c="${rest%%/*}"; p="$p/$c"
        [ -L "$p" ] && { linked=1; break; }
        case "$rest" in */*) rest="${rest#*/}" ;; *) rest="" ;; esac
      done
      if [ -n "$linked" ]; then echo "[x] refusing to remove '$MODELS/$k' (symlink in path)" >&2; FAILED=1; continue; fi
      rm_path "$MODELS/$k"
    done <<< "$keys"
  fi
  # Kokoro folder: only when it is a safe target strictly inside the models folder (KOKORO_DIR is overridable)
  local kd mc
  if [ -d "$KOKORO_DIR" ] && safe_target "$KOKORO_DIR"; then
    kd="$(cd "$KOKORO_DIR" && pwd -P)"; mc="$(cd "$MODELS" && pwd -P)"
    case "$kd/" in "$mc/"?*) rm_path "$KOKORO_DIR" ;; *) echo "[x] refusing to remove KOKORO_DIR '$KOKORO_DIR' (not inside $MODELS)" >&2; FAILED=1 ;; esac
  elif [ -e "$KOKORO_DIR" ]; then
    echo "[x] refusing to remove KOKORO_DIR '$KOKORO_DIR'" >&2; FAILED=1
  fi
  # keep the manifest for a retry when any deletion above failed
  [ -n "$FAILED" ] || rm_path "$MODELS/manifest.json"
  rmdir "$MODELS/whisper-cpp" "$MODELS/opus" 2>/dev/null
  if rmdir "$MODELS" 2>/dev/null; then say "removed $MODELS"; else say "kept other files in $MODELS (not tinglongWu's)"; fi
}

if [ -n "$PURGE" ] && [ -d "$MODELS" ]; then
  purge_models
  # foreign files left in a models folder that lives inside a folder we are about to remove: refuse, never delete them
  if [ -d "$MODELS" ]; then
    m="$(cd "$MODELS" && pwd -P)"
    for t in "$DIR" "$KOKORO_VENV"; do
      [ -d "$t" ] || continue
      c="$(cd "$t" && pwd -P)"
      case "$m/" in "$c/"*) echo "[x] $MODELS still holds files that are not tinglongWu's and is inside $t; move them or the folder first" >&2; exit 1 ;; esac
    done
  fi
fi
rm_path "$DIR"
rm_path "$KOKORO_VENV"
# launcher written by mac_install.sh (only if it is ours)
if grep -qs 'generated by mac_install.sh' "$BIN_DIR/tlwu-menu" 2>/dev/null; then rm_path "$BIN_DIR/tlwu-menu"; fi
# #1678: app-menu shortcut written by install.sh (only if it is ours)
if grep -qs '^Name=tinglongWu$' "$HOME/.local/share/applications/tinglongwu.desktop" 2>/dev/null; then rm_path "$HOME/.local/share/applications/tinglongwu.desktop"; fi
# PATH line added by mac_install.sh: write the filtered copy first, replace the rc file only if that fully succeeded
for rc in "$HOME/.zprofile" "$HOME/.zshrc"; do
  if grep -qs 'added by tinglongWu' "$rc"; then
    tmp="$(mktemp)" || { echo "[x] cannot edit $rc (mktemp failed)" >&2; FAILED=1; continue; }
    if { grep -v 'added by tinglongWu' "$rc" > "$tmp"; [ $? -le 1 ]; } && cat "$tmp" > "$rc"; then
      say "removed the PATH line from $rc"
    else
      echo "[x] could not update $rc" >&2; FAILED=1
    fi
    rm -f "$tmp"
  fi
done

if [ -d "$MODELS" ]; then
  if [ -n "$PURGE" ]; then :   # already purged before the app folder was removed
  elif [ -z "$YES" ] && ( : </dev/tty ) 2>/dev/null; then
    read -rp "Also delete downloaded models in $MODELS? [y/N] " ans </dev/tty || ans=""
    case "$ans" in y|Y|yes|YES) purge_models ;; *) say "kept $MODELS (use --purge to remove)" ;; esac
  else say "kept $MODELS (use --purge to remove)"; fi
fi
if [ "$(uname -s)" = "Darwin" ]; then
  echo "BlackHole was not touched. To remove it: brew uninstall --cask blackhole-2ch blackhole-16ch"
fi
[ -z "$FAILED" ] || { echo "[x] uninstall incomplete" >&2; exit 1; }
say "tinglongWu uninstalled."
