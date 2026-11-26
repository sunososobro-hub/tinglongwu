#!/usr/bin/env bash
# #1728: host side of mobile/integration_test/mic_test.dart, run by .github/workflows/mobile-emulator.yml.
# The emulator (started with -allow-host-audio) records from the host's default PulseAudio source; here that
# source is the monitor of a null sink, so whatever is played into the sink is the emulator's microphone.
#   host_mic.sh setup                  start PulseAudio (socket in $TLWU_PULSE_DIR) with null sink tlwu_clip,
#                                      its monitor as the default source; prints PULSE_SERVER=... for GITHUB_ENV
#   host_mic.sh play <wav>             loop <wav> into the sink in the background
#   host_mic.sh stop                   stop that loop (the source then gives digital silence)
#   host_mic.sh rms [sec]              record [sec] (default 1) s from the default source, print RMS dBFS
#   host_mic.sh serve <log> <wav>      handshake with mic_test.dart (needs ADB, PKG, E2E_DIR in the env):
#                                      phase=ready -> grant RECORD_AUDIO, play, touch mic_go;
#                                      phase=clip_done -> stop, touch mic_silent; exits after that
# The phase lines come from the test's print(); they are looked for in <log> (flutter test output) and in
# logcat, whichever shows them first.
set -euo pipefail
DIR="${TLWU_PULSE_DIR:-${RUNNER_TEMP:-/tmp}/tlwu-pulse}"
export PULSE_SERVER="unix:$DIR/native"
mkdir -p "$DIR"
SINK=tlwu_clip

case "${1:-}" in
  setup)
    mkdir -p "$DIR"
    # -n: no default.pa (no hardware modules on a runner); the socket path is pinned so every later step
    # (emulator, paplay) finds the same server through PULSE_SERVER.
    pulseaudio -D -n --exit-idle-time=-1 --log-target=file:"$DIR/pulse.log" \
      --load="module-native-protocol-unix socket=$DIR/native auth-anonymous=1" \
      --load="module-null-sink sink_name=$SINK rate=48000 channels=2"
    for _ in $(seq 1 50); do pactl info > /dev/null 2>&1 && break; sleep 0.2; done
    pactl set-default-sink "$SINK"
    pactl set-default-source "$SINK.monitor"
    pactl info | grep -E '^Default (Sink|Source)' >&2
    echo "PULSE_SERVER=$PULSE_SERVER"
    ;;
  play)
    wav="${2:?wav}"
    test -f "$wav"
    "$0" stop
    ( while true; do paplay --device="$SINK" "$wav" || sleep 1; done ) > /dev/null 2>&1 &
    echo $! > "$DIR/play.pid"
    ;;
  stop)
    if [ -f "$DIR/play.pid" ]; then
      pid=$(cat "$DIR/play.pid")
      pkill -P "$pid" 2> /dev/null || true
      kill "$pid" 2> /dev/null || true
      rm -f "$DIR/play.pid"
    fi
    pkill -f "paplay --device=$SINK" 2> /dev/null || true
    ;;
  rms)
    sec="${2:-1}"
    raw="$DIR/rms.raw"
    timeout "$sec" parec --latency-msec=20 --format=s16le --rate=16000 --channels=1 > "$raw" || true
    python3 - "$raw" << 'EOF'
import math, struct, sys
b = open(sys.argv[1], "rb").read()
n = len(b) // 2
s = struct.unpack("<%dh" % n, b[: n * 2])
ms = sum(x * x for x in s) / n / 32768.0 ** 2 if n else 0.0
if not n:
    sys.exit("host_mic rms: no samples recorded")
print("%.1f dBFS (%d samples)" % (10 * math.log10(ms) if ms > 1e-12 else -120.0, n))
EOF
    ;;
  serve)
    log="${2:?log}"; wav="${3:?wav}"
    : "${ADB:?}" "${PKG:?}" "${E2E_DIR:?}"
    seen() { grep -q "TLWU_MIC_PHASE=$1" "$log" 2> /dev/null || "$ADB" logcat -d -s flutter 2> /dev/null | grep -q "TLWU_MIC_PHASE=$1"; }
    wait_phase() {
      for _ in $(seq 1 1800); do seen "$1" && return 0; sleep 1; done
      echo "host_mic: no TLWU_MIC_PHASE=$1 within 30 min" >&2; return 1
    }
    wait_phase ready
    "$ADB" shell pm grant "$PKG" android.permission.RECORD_AUDIO
    "$0" play "$wav"
    sleep 1
    "$ADB" shell touch "$E2E_DIR/mic_go"
    echo "host_mic: clip playing, mic_go sent" >&2
    wait_phase clip_done
    "$0" stop
    sleep 1
    "$ADB" shell touch "$E2E_DIR/mic_silent"
    echo "host_mic: clip stopped, mic_silent sent" >&2
    ;;
  *)
    sed -n '2,14p' "$0" >&2; exit 2
    ;;
esac
