#!/bin/bash
# Build a double-clickable macOS app that opens the tinglongWu GUI (floating subtitle bar + control window).
#
#   scripts/make_mac_app.sh              -> ~/Applications/TingLongWu.app
#   scripts/make_mac_app.sh /some/folder -> /some/folder/TingLongWu.app
#
# The app is only a launcher: it runs this repo's .venv (`python -m tinglongwu.gui`), so keep the repo where it is.
# Re-run the script after moving the repo. To remove the app, delete the .app folder.
# Log of the last run: ~/Library/Logs/TingLongWu.log
set -eu

REPO="$(cd "$(dirname "$0")/.." && pwd)"
DEST_DIR="${1:-$HOME/Applications}"
APP="$DEST_DIR/TingLongWu.app"

[ -x "$REPO/.venv/bin/python" ] || { echo "No $REPO/.venv found. Create it first (python3 -m venv .venv && pip install -r requirements.txt && pip install -e . --no-deps)." >&2; exit 1; }

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"

cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>TingLongWu</string>
  <key>CFBundleDisplayName</key><string>聽攏有</string>
  <key>CFBundleIdentifier</key><string>local.tinglongwu.launcher</string>
  <key>CFBundleExecutable</key><string>launcher</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>LSMinimumSystemVersion</key><string>12.0</string>
  <key>NSMicrophoneUsageDescription</key>
  <string>tinglongWu listens to the BlackHole audio device (system sound) to translate it locally.</string>
</dict>
</plist>
PLIST

cat > "$APP/Contents/MacOS/launcher" <<LAUNCH
#!/bin/bash
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
cd "$REPO"
# No `exec`: keep this launcher as the app's own process and run Python as its child, so macOS attributes the
# microphone permission to TingLongWu.app (with exec the process becomes plain Python and no prompt appears).
"$REPO/.venv/bin/python" -m tinglongwu.gui >> "\$HOME/Library/Logs/TingLongWu.log" 2>&1
LAUNCH
chmod +x "$APP/Contents/MacOS/launcher"

# ad-hoc signature so macOS can attach the microphone permission to this app (harmless if codesign is missing)
command -v codesign >/dev/null 2>&1 && codesign --force --deep --sign - "$APP" >/dev/null 2>&1 || true

echo "Created: $APP"
echo "Double-click it, or drag it to the Dock. First start: allow Microphone when macOS asks."
echo "If nothing opens, read: ~/Library/Logs/TingLongWu.log"
