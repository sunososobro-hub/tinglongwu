#!/usr/bin/env bash
# #1728: host side of mobile/integration_test/mic_test.dart, run by .github/workflows/mobile-emulator.yml.
# The emulator (started with -allow-host-audio) records from the host's default PulseAudio source; here that
# source is the monitor of a null sink, so whatever is played into the sink is the emulator's microphone.
#   host_mic.sh setup                  start the user's PulseAudio daemon (default socket, so the emulator's own
#                                      libpulse finds it) with null sink tlwu_clip, its monitor the default source
#   host_mic.sh play <wav>             loop <wav> into the sink in the background
#   host_mic.sh stop                   stop that loop (the source then gives digital silence)
#   host_mic.sh route                  host mic on in the emulator (`adb emu avd hostmicon`, needs ADB), every
#                                      PulseAudio record stream moved to the sink's monitor, unmuted at 100 %;
#                                      what PulseAudio reports is appended to $DIR/source-outputs.txt
#   host_mic.sh rms [sec]              record [sec] (default 1) s from the default source, print RMS dBFS
#   host_mic.sh serve <wav>            handshake with mic_test.dart through files in $E2E_DIR on the device
#                                      (needs ADB, PKG, E2E_DIR in the env): mic_ready -> grant RECORD_AUDIO,
#                                      play, route, touch mic_go, route again; mic_clip_done -> stop, touch mic_silent; then exits
# Files, not print() lines: `flutter test` hands an integration test's prints to the host only when the test
# ends (run 45861986791 saw TLWU_MIC_PHASE=ready 3.5 min late), so a printed phase can never be answered.
set -euo pipefail
DIR="${TLWU_PULSE_DIR:-${RUNNER_TEMP:-/tmp}/tlwu-pulse}"
mkdir -p "$DIR"
SINK=tlwu_clip

case "${1:-}" in
  setup)
    # The default daemon and socket ($XDG_RUNTIME_DIR/pulse/native): run 45861986791 started PulseAudio on a
    # custom socket named by PULSE_SERVER and the emulator's bundled libpulse still failed ("Failed to
    # initialize PA context"), so every client here uses the server it would find with no settings at all.
    pulseaudio --check 2> /dev/null || pulseaudio -D --exit-idle-time=-1 --log-level=info --log-target=file:"$DIR/pulse.log"
    for _ in $(seq 1 50); do pactl info > /dev/null 2>&1 && break; sleep 0.2; done
    pactl list short sinks | grep -q "^[0-9]*[[:space:]]$SINK[[:space:]]" \
      || pactl load-module module-null-sink sink_name="$SINK" rate=48000 channels=2 > /dev/null
    pactl set-default-sink "$SINK"
    pactl set-default-source "$SINK.monitor"
    pactl info | grep -E '^(Server String|Default Sink|Default Source)' >&2
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
  route)
    # Round 3: run 45881839662 (windowed qemu on Xvfb) showed qemu opening a PulseAudio record stream, yet the
    # guest measured -76.5 dBFS both with the clip playing and without (no digital zeros either), so host audio
    # never reached it. Each candidate cause is undone here: the emulator's runtime host-mic switch (console
    # `avd hostmicon`), a stream on another source, a muted / zero-volume stream or monitor. Best effort: a
    # missing tool never fails the test, and the listing tells the next round which cause it was.
    out="$DIR/source-outputs.txt"
    if [ -n "${ADB:-}" ]; then
      echo "hostmicon: $("$ADB" emu avd hostmicon 2>&1 | tr -d '\r' | tr '\n' ' ')" >> "$out" || true
    fi
    pactl set-source-mute "$SINK.monitor" 0 > /dev/null 2>&1 || true
    pactl set-source-volume "$SINK.monitor" 100% > /dev/null 2>&1 || true
    ids=$(pactl list short source-outputs 2> /dev/null | awk '{print $1}' || true)
    for id in $ids; do
      pactl move-source-output "$id" "$SINK.monitor" > /dev/null 2>&1 || true
      pactl set-source-output-mute "$id" 0 > /dev/null 2>&1 || true
      pactl set-source-output-volume "$id" 100% > /dev/null 2>&1 || true
    done
    {
      echo "record streams: $(echo $ids | wc -w)"
      pactl list source-outputs 2> /dev/null | sed 's/^[[:space:]]*//' \
        | grep -E '^(Source Output #|Source:|Corked:|Mute:|Volume:|Sample Specification:|application.process.binary)' \
        | tr '\n' ' ' | sed 's/Source Output #/\nSource Output #/g'
      echo
      pactl list short sources 2> /dev/null | awk '{print "source " $1 " " $2 " " $NF}'
    } >> "$out" 2>&1 || true
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
    wav="${2:?wav}"
    : "${ADB:?}" "${PKG:?}" "${E2E_DIR:?}"
    seen() { "$ADB" shell ls "$E2E_DIR" 2> /dev/null | tr -d '\r' | grep -qx "$1"; }
    wait_file() {
      for _ in $(seq 1 1800); do seen "$1" && return 0; sleep 1; done
      echo "host_mic: no $E2E_DIR/$1 within 30 min" >&2; return 1
    }
    wait_file mic_ready
    "$ADB" shell pm grant "$PKG" android.permission.RECORD_AUDIO
    "$0" play "$wav"
    "$0" route
    sleep 1
    "$ADB" shell touch "$E2E_DIR/mic_go"
    echo "host_mic: clip playing, mic_go sent" >&2
    # qemu may open its record stream only when the guest starts recording, so route once more after mic_go.
    sleep 1
    "$0" route
    wait_file mic_clip_done
    "$0" stop
    sleep 1
    "$ADB" shell touch "$E2E_DIR/mic_silent"
    echo "host_mic: clip stopped, mic_silent sent" >&2
    ;;
  *)
    sed -n '2,17p' "$0" >&2; exit 2
    ;;
esac
