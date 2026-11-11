#!/usr/bin/env bash
# One-click Kokoro English TTS install for Linux (port of the Kokoro step in mac_install.sh). Safe to re-run.
#   bash scripts/linux_kokoro.sh
# Creates ~/tts-kokoro (own venv: kokoro-onnx pulls in GPL espeak, so it stays out of the main .venv) and downloads
# the two model files into ~/models/kokoro, checking size AND SHA256 (a mismatch deletes the file and aborts).
# linux_menu.sh finds exactly these paths (KOKORO_PY / KOKORO_MODEL), so no manual configuration is needed.
# Env overrides (mainly for tests): KOKORO_VENV, KOKORO_DIR, KOKORO_BASE, KOKORO_ONNX_SIZE/SHA256,
# KOKORO_VOICES_SIZE/SHA256, TLWU_KOKORO_SKIP_PIP=1 (skip pip, tests only), PYTHON.
set -u

KOKORO_VENV="${KOKORO_VENV:-$HOME/tts-kokoro}"
KOKORO_DIR="${KOKORO_DIR:-$HOME/models/kokoro}"
KOKORO_BASE="${KOKORO_BASE:-https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0}"
# Sizes are the same as in mac_install.sh. SHA256 computed from a download of the v1.0 release files whose sizes match.
KOKORO_ONNX_SIZE="${KOKORO_ONNX_SIZE:-92361271}"
KOKORO_ONNX_SHA256="${KOKORO_ONNX_SHA256:-6e742170d309016e5891a994e1ce1559c702a2ccd0075e67ef7157974f6406cb}"
KOKORO_VOICES_SIZE="${KOKORO_VOICES_SIZE:-28214398}"
KOKORO_VOICES_SHA256="${KOKORO_VOICES_SHA256:-bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d}"

say()  { printf '\n==> %s\n' "$*"; }
info() { printf '    %s\n' "$*"; }
die()  { printf '\n[x] %s\n' "$*" >&2; exit 1; }
fsize() { wc -c < "$1" 2>/dev/null | tr -d ' '; }
sha256_of() { sha256sum "$1" | cut -d' ' -f1; }

PY="${PYTHON:-}"
if [ -z "$PY" ]; then
  for c in python3.12 python3.11 python3.10 python3; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if (3,10)<=sys.version_info[:2]<=(3,12) else 1)' 2>/dev/null; then PY="$c"; break; fi
  done
fi
[ -n "$PY" ] || die "python3 3.10-3.12 with venv is required for the Kokoro environment."
command -v curl >/dev/null 2>&1 || die "curl is required."
command -v sha256sum >/dev/null 2>&1 || die "sha256sum is required."

say "Kokoro English voice (separate venv: $KOKORO_VENV)"
if [ ! -x "$KOKORO_VENV/bin/python" ]; then
  "$PY" -m venv "$KOKORO_VENV" || die "cannot create $KOKORO_VENV (Debian/Ubuntu: sudo apt install python3-venv)."
fi
if [ -z "${TLWU_KOKORO_SKIP_PIP:-}" ]; then
  "$KOKORO_VENV/bin/python" -m pip install -q --upgrade pip || info "pip upgrade failed (ignored)."
  "$KOKORO_VENV/bin/python" -m pip install "kokoro-onnx==0.6.1" || die "pip install kokoro-onnx==0.6.1 failed."
  "$KOKORO_VENV/bin/python" -c "import kokoro_onnx" || die "kokoro_onnx import failed."
fi

mkdir -p "$KOKORO_DIR" || die "cannot create $KOKORO_DIR"
fetch_model() {  # name expected_size expected_sha256
  local name="$1" want="$2" sha="$3" got
  if [ -f "$KOKORO_DIR/$name" ] && [ "$(fsize "$KOKORO_DIR/$name")" = "$want" ] && [ "$(sha256_of "$KOKORO_DIR/$name")" = "$sha" ]; then
    info "$name already present"; return 0
  fi
  rm -f "$KOKORO_DIR/$name" "$KOKORO_DIR/$name.part"
  info "downloading $name (~$((want / 1000000)) MB) ..."
  curl -fL --retry 3 --retry-delay 3 -o "$KOKORO_DIR/$name.part" "$KOKORO_BASE/$name" || { rm -f "$KOKORO_DIR/$name.part"; die "download of $name failed (network?)."; }
  got="$(fsize "$KOKORO_DIR/$name.part")"
  [ "$got" = "$want" ] || { rm -f "$KOKORO_DIR/$name.part"; die "$name has wrong size (got $got, expected $want); deleted."; }
  [ "$(sha256_of "$KOKORO_DIR/$name.part")" = "$sha" ] || { rm -f "$KOKORO_DIR/$name.part"; die "$name SHA256 mismatch; deleted (refusing to use it)."; }
  mv "$KOKORO_DIR/$name.part" "$KOKORO_DIR/$name" || die "cannot save $name"
}
fetch_model kokoro-v1.0.int8.onnx "$KOKORO_ONNX_SIZE" "$KOKORO_ONNX_SHA256"
fetch_model voices-v1.0.bin "$KOKORO_VOICES_SIZE" "$KOKORO_VOICES_SHA256"
( cd "$KOKORO_DIR" && sha256sum kokoro-v1.0.int8.onnx voices-v1.0.bin > SHA256SUMS ) 2>/dev/null || true
info "Kokoro ready: $KOKORO_VENV/bin/python, $KOKORO_DIR"
