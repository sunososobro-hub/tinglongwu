"""#1159 (Chrome extension part 2, parent #711): the unpacked MV3 extension in extension/capture captures a tab's
audio with chrome.tabCapture, keeps the tab audible and hands 16 kHz mono frames to the recognizer hook.

The capture test loads the extension in Playwright's Chromium, plays a 3 s 440 Hz WAV in a local page and reads the
recognizer hook's counters from the offscreen document. CI runs it in the `test` job with TLWU_REQUIRE_EXTENSION=1
(a skip there is a failure); elsewhere it skips when Playwright / Chromium / node are missing.

Cloud check of playback after capture starts (#1159 cloud-verify, chosen by the owner over a hand test): with
TLWU_PULSE_MONITOR=<PulseAudio source> (the monitor of the null sink that stands in for the speakers, set by the
`extension-capture` workflow) the test also records that monitor with `parec` while the tab plays after capture has
started, and asserts the 440 Hz tone reached the speakers for >= 90 % of the clip. It does not record across the
moment capture starts on an already-playing tab; it also checks the action badge reads ON while capturing and
is cleared on stop.

Test-only Chromium switch: `--allowlisted-extension-id` stands in for the action click (Chrome only grants
tabCapture to an extension the user invoked on that tab; automation cannot click the toolbar).
"""
from __future__ import annotations

import functools
import hashlib
import http.server
import json
import math
import os
import shutil
import struct
import subprocess
import threading
import time
import wave
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
EXT = ROOT / "extension" / "capture"
REQUIRE = os.environ.get("TLWU_REQUIRE_EXTENSION") == "1"
PULSE_MONITOR = os.environ.get("TLWU_PULSE_MONITOR", "")
CLIP_S, TONE_HZ, CLIP_RATE = 3.0, 440.0, 48000
RATE = 16000


def _need(reason):
    if REQUIRE:
        pytest.fail(f"TLWU_REQUIRE_EXTENSION=1 but {reason}")
    pytest.skip(reason)


def _extension_id(path: Path) -> str:
    """Chromium's id for an unpacked extension: sha256 of its absolute path, first 32 hex digits mapped 0-f -> a-p."""
    h = hashlib.sha256(str(path).encode()).hexdigest()[:32]
    return "".join(chr(ord("a") + int(c, 16)) for c in h)


def _tone_wav(path: Path):
    n = int(CLIP_RATE * CLIP_S)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(CLIP_RATE)
        w.writeframes(b"".join(struct.pack("<hh", s, s) for s in
                               (int(0.5 * 32767 * math.sin(2 * math.pi * TONE_HZ * i / CLIP_RATE)) for i in range(n))))


# --- manifest: no network, no host access ------------------------------------------------------------------------

def test_manifest_permissions_are_tab_capture_and_offscreen_only():
    m = json.loads((EXT / "manifest.json").read_text("utf-8"))
    assert m["manifest_version"] == 3
    assert sorted(m["permissions"]) == ["offscreen", "storage", "tabCapture"]
    for key in ("host_permissions", "optional_host_permissions", "optional_permissions", "externally_connectable",
                "web_accessible_resources"):
        assert key not in m, key
    # #1160: the only content script is the inert subtitle overlay, on https meeting sites only
    assert [cs["js"] for cs in m["content_scripts"]] == [["overlay.js"]]
    assert all(x.startswith("https://") for cs in m["content_scripts"] for x in cs["matches"])
    # default MV3 CSP (self only) or an explicit one without remote sources
    csp = json.dumps(m.get("content_security_policy", {}))
    assert "http" not in csp and "*" not in csp
    # every file the manifest / pages load exists in the package (MV3: no remote code)
    for f in (m["background"]["service_worker"], "offscreen.html", "offscreen.js", "pcm-worklet.js", "resample.js",
              "recognizer.js", "overlay.js"):
        assert (EXT / f).is_file(), f


# --- resampler (node, the same ES module the worklet imports) ----------------------------------------------------

_NODE_CHECK = r"""
import { Downsampler, toMono } from %s;
const out = {};
for (const [rate, block] of [[48000, 128], [44100, 128], [44100, 441], [16000, 160], [96000, 1000]]) {
  const ds = new Downsampler(rate);
  const n = rate * 2; // 2 s of a 440 Hz tone, pushed in render-quantum blocks
  let total = 0, zc = 0, last = 0, peak = 0;
  for (let i = 0; i < n; i += block) {
    const m = Math.min(block, n - i);
    const x = new Float32Array(m);
    for (let j = 0; j < m; j++) x[j] = 0.5 * Math.sin(2 * Math.PI * 440 * (i + j) / rate);
    const y = ds.push(x);
    total += y.length;
    for (const v of y) { if ((v >= 0) !== (last >= 0)) zc++; last = v; peak = Math.max(peak, Math.abs(v)); }
  }
  out[`${rate}/${block}`] = { total, zc, peak };
}
let threw = false;
try { new Downsampler(8000); } catch (e) { threw = e instanceof RangeError; }
out.below = threw;
out.mono = Array.from(toMono([Float32Array.of(1, 0), Float32Array.of(0, 1)]));
console.log(JSON.stringify(out));
"""


def test_downsampler_keeps_count_and_pitch_for_every_input_rate(tmp_path):
    node = shutil.which("node")
    if not node:
        _need("node is not installed")
    script = tmp_path / "check.mjs"
    script.write_text(_NODE_CHECK % json.dumps((EXT / "resample.js").as_uri()), encoding="utf-8")
    r = subprocess.run([node, str(script)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    for key in ("48000/128", "44100/128", "44100/441", "16000/160", "96000/1000"):
        st = out[key]
        assert abs(st["total"] - 2 * RATE) <= 1, (key, st)  # no drift, no dropped block remainders
        assert abs(st["zc"] - 2 * 2 * 440) <= 4, (key, st)  # 440 Hz survives: 880 zero crossings per second
        assert 0.45 < st["peak"] <= 0.5, (key, st)
    assert out["below"] is True  # upsampling is refused, not silently wrong
    assert out["mono"] == [0.5, 0.5]


# --- what reaches the speakers (PulseAudio null-sink monitor) -----------------------------------------------------

MON_RATE = 16000
MON_WIN, MON_HOP = 1600, 160  # 100 ms pitch windows, slid in 10 ms steps
# a 0.5 ms run of near-zero samples is a dropout: a clean 440 Hz tone at the weakest counted level (min_peak) stays
# below min_peak / 5 for ~2.3 samples around each zero crossing, louder tones for less
MON_GAP = 8


def tone_seconds(pcm: bytes, rate: int = MON_RATE, hz: float = TONE_HZ, min_peak: float = 0.05) -> float:
    """Seconds of a recording (s16le mono) that carry the test tone, at sample resolution.

    A sample counts when it lies in a 100 ms window (any 10 ms offset) whose peak is >= `min_peak` (full scale 1.0)
    and whose zero-crossing rate is within 10 % of 2 * hz, and it is not part of a >= 0.5 ms run of samples below
    min_peak / 5 (a dropout inside the tone)."""
    import numpy as np
    x = np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype="<i2").astype(np.float32) / 32768.0
    n = len(x)
    covered = np.zeros(n, dtype=bool)
    sign = x >= 0
    flips = np.concatenate(([0], np.cumsum(sign[1:] != sign[:-1])))  # flips[j] = zero crossings in x[0..j]
    for i in range(0, n - MON_WIN + 1, MON_HOP):
        if np.max(np.abs(x[i:i + MON_WIN])) < min_peak:
            continue
        zc = flips[i + MON_WIN - 1] - flips[i]
        if abs(zc * rate / MON_WIN - 2 * hz) <= 0.1 * 2 * hz:
            covered[i:i + MON_WIN] = True
    quiet = np.abs(x) < min_peak / 5
    edges = np.flatnonzero(np.diff(np.concatenate(([0], quiet.astype(np.int8), [0]))))
    for start, end in zip(edges[::2], edges[1::2]):
        if end - start >= MON_GAP:
            covered[start:end] = False
    return float(np.count_nonzero(covered)) / rate


def test_tone_seconds_measures_tone_at_sample_resolution():
    def pcm(sig):
        return b"".join(struct.pack("<h", int(32767 * v)) for v in sig)

    def sine(f, secs, a=0.3, phase=0):
        return [a * math.sin(2 * math.pi * f * (i + phase) / MON_RATE) for i in range(int(secs * MON_RATE))]
    tone = sine(TONE_HZ, 2.0)
    assert tone_seconds(pcm(tone)) == pytest.approx(2.0, abs=0.01)
    assert tone_seconds(pcm(sine(1000, 1.0))) == 0.0  # wrong pitch
    assert tone_seconds(pcm([0.01 * v for v in tone[:MON_RATE]])) == 0.0  # below the level of an audible tab
    assert tone_seconds(pcm([0.0] * MON_RATE + tone + sine(1000, 1.0))) == pytest.approx(2.0, abs=0.06)
    # a continuous tone that starts mid-window is measured whole
    assert tone_seconds(pcm([0.0] * 800 + sine(TONE_HZ, 2.7))) == pytest.approx(2.7, abs=0.01)
    # 27 x (92 ms tone + 8 ms dropout) is 2.484 s of tone, below the 2.7 s (90 % of 3 s) bar
    chopped = []
    for k in range(27):
        chopped += sine(TONE_HZ, 0.092, phase=k * 1600) + [0.0] * 128
    got = tone_seconds(pcm(chopped))
    assert got == pytest.approx(2.484, abs=0.02) and got < 0.9 * CLIP_S
    # 11 x 1.5 ms dropouts per 100 ms, centred on rising zero crossings (crossing count unchanged): 2.505 s of 3 s
    holey = sine(TONE_HZ, 3.0)
    period = MON_RATE / TONE_HZ
    for k in range(int(3.0 * TONE_HZ)):
        if k % 4 == 0:
            c = round(k * period)
            for j in range(max(0, c - 12), min(len(holey), c + 12)):
                holey[j] = 0.0
    got = tone_seconds(pcm(holey))
    assert got < 0.9 * CLIP_S and got == pytest.approx(2.505, abs=0.03), got
    # the MON_GAP boundary: an 8-sample silent run (on a positive peak, no extra crossing) is cut, a 7-sample one kept
    base = sine(TONE_HZ, 2.0)
    peak = round(20.25 * MON_RATE / TONE_HZ)  # a positive sine peak, away from any zero crossing
    full = tone_seconds(pcm(base))
    for run, lost in ((MON_GAP, MON_GAP), (MON_GAP - 1, 0)):
        cut = list(base)
        cut[peak - run // 2: peak - run // 2 + run] = [0.0] * run
        assert tone_seconds(pcm(cut)) == pytest.approx(full - lost / MON_RATE, abs=0.5 / MON_RATE), run
    # the weakest counted tone keeps its full length (its near-zero runs are shorter than MON_GAP)
    assert tone_seconds(pcm(sine(TONE_HZ, 2.0, a=0.06))) == pytest.approx(2.0, abs=0.01)


class _MonitorRecorder:
    """parec on the null-sink monitor: what a user would hear from the speakers."""

    def __init__(self, source: str, ready_timeout: float = 10.0):
        parec = shutil.which("parec")
        if not parec:
            pytest.fail(f"TLWU_PULSE_MONITOR={source} but parec is not installed")
        self.proc = subprocess.Popen([parec, "-d", source, "--raw", "--format=s16le", f"--rate={MON_RATE}",
                                      "--channels=1", "--latency-msec=50"], stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE)
        self.chunks: list[bytes] = []
        self.first = threading.Event()
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()
        # the record stream is live only once PCM arrives: never start playback before that
        # EOF also sets the event (parec exited): ready only when a PCM chunk really arrived
        if not self.first.wait(ready_timeout) or not self.chunks:
            err = self.close()
            pytest.fail(f"parec on {source} delivered no audio within {ready_timeout} s: {err}")

    def _read(self):
        while True:
            b = self.proc.stdout.read(320)
            if not b:
                self.first.set()
                return
            self.chunks.append(b)
            self.first.set()

    def close(self) -> str:
        """Terminate and reap parec, finish the reader; idempotent. Returns parec's stderr. If parec had already
        exited on its own (before this first close), `self.died` holds its exit status and stderr."""
        if hasattr(self, "err"):
            return self.err
        code = self.proc.poll()  # one status read decides both the diagnosis and the shutdown
        if code is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)
        self.thread.join(timeout=5)
        self.err = self.proc.stderr.read().decode(errors="replace") if self.proc.stderr else ""
        self.died = None if code is None else (code, self.err)
        return self.err

    def data(self) -> bytes:
        return b"".join(self.chunks)


# --- end to end in Chromium ---------------------------------------------------------------------------------------

@pytest.fixture()
def tone_page(tmp_path):
    site = tmp_path / "site"
    site.mkdir()
    _tone_wav(site / "tone.wav")
    (site / "index.html").write_text(
        '<!doctype html><meta charset="utf-8"><title>meeting</title><audio id="a" src="tone.wav" preload="auto">'
        "</audio><button id=\"play\" onclick=\"document.getElementById('a').play()\">play</button>", encoding="utf-8")
    handler = functools.partial(QuietHandler, directory=str(site))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_port}/index.html"
    finally:
        srv.shutdown()


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def _launch(p, user_dir: Path, ext: Path):
    ext_id = _extension_id(ext)
    args = [f"--disable-extensions-except={ext}", f"--load-extension={ext}",
            f"--allowlisted-extension-id={ext_id}"]
    kw = {"channel": "chromium", "headless": True, "args": args,
          # Playwright mutes headless audio by default; a muted tab captures silence, like a muted meeting
          "ignore_default_args": ["--mute-audio"]}
    if os.environ.get("TLWU_CHROMIUM"):
        kw.pop("channel")
        kw["executable_path"] = os.environ["TLWU_CHROMIUM"]
    try:
        ctx = p.chromium.launch_persistent_context(str(user_dir), **kw)
    except Exception as e:  # browser not installed
        _need(f"chromium unavailable: {e}")
    sw = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker", timeout=30000)
    assert sw.url.startswith(f"chrome-extension://{ext_id}/"), (sw.url, ext_id)
    return ctx, sw


def test_tab_capture_streams_16k_frames_and_keeps_the_tab_audible(tmp_path, tone_page):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        _need("playwright is not installed")
    ext = tmp_path / "ext"
    shutil.copytree(EXT, ext)  # a fixed path -> a known extension id for the allowlist switch
    with sync_playwright() as p:
        ctx, sw = _launch(p, tmp_path / "profile", ext.resolve())
        mon = None
        try:
            page = ctx.new_page()
            page.goto(tone_page)
            page.wait_for_function("document.getElementById('a').readyState >= 4")
            if PULSE_MONITOR:
                # a meeting tab is already audible when the user clicks the action. On a real PulseAudio sink the
                # tab's very first output stream opened under capture loses 0.4-1.6 s (82 %, 46 % voiced in 2 of 5
                # local runs); one plain play first makes it 5 of 5 >= 99 %. That first-stream loss has its own issue.
                page.click("#play")
                page.wait_for_function("document.getElementById('a').ended", timeout=20000)
                page.evaluate("document.getElementById('a').currentTime = 0")
            started = sw.evaluate("""async () => {
                const [t] = await chrome.tabs.query({active: true, lastFocusedWindow: true});
                return await tlwu.startCapture(t.id);
            }""")
            assert started["ok"] is True, started
            badge_on = sw.evaluate("""async () => {
                const [t] = await chrome.tabs.query({active: true, lastFocusedWindow: true});
                return await chrome.action.getBadgeText({tabId: t.id});
            }""")
            mon = _MonitorRecorder(PULSE_MONITOR) if PULSE_MONITOR else None
            heard = None
            page.click("#play")  # a real click: the default autoplay policy, as for a user
            t0 = time.monotonic()
            page.wait_for_function("document.getElementById('a').ended", timeout=20000)
            played_s = time.monotonic() - t0
            page_state = page.evaluate("(() => { const a = document.getElementById('a');"
                                       " return {t: a.currentTime, d: a.duration, muted: a.muted}; })()")
            time.sleep(0.5)  # the last frames are still in the worklet / message queue
            if mon:
                mon.close()
                if mon.died is not None:  # the recorder stopped by itself: not a verdict on what the tab played
                    pytest.fail(f"parec exited early with status {mon.died[0]}: {mon.died[1]}")
                heard = mon.data()
            st = sw.evaluate("() => tlwu.captureStats()")
            stopped = sw.evaluate("() => tlwu.stopCapture()")
            after = sw.evaluate("() => tlwu.captureStats()")
            badge_off = sw.evaluate("""async () => {
                const [t] = await chrome.tabs.query({active: true, lastFocusedWindow: true});
                return await chrome.action.getBadgeText({tabId: t.id});
            }""")
        finally:
            if mon:
                mon.close()  # also when playback failed: never leave parec running
            ctx.close()
    rec = st["recognizer"]
    expected = int(CLIP_S * RATE)
    assert st["capturing"] is True and st["contextState"] == "running", st
    assert rec["samples"] == rec["frames"] * 1600, rec  # whole 100 ms frames at 16 kHz
    # >= 90 % of the clip's 16 kHz samples reached the recognizer hook with sound in them (and not more than the clip)
    assert 0.9 * expected <= rec["voicedSamples"] <= 1.1 * expected, (rec, expected)
    # the pitch survived the 48k/44.1k -> 16k resampling: 440 Hz = 880 zero crossings per voiced second
    zc_per_s = rec["zeroCrossings"] / (rec["voicedSamples"] / RATE)
    assert abs(zc_per_s - 2 * TONE_HZ) / (2 * TONE_HZ) < 0.05, (zc_per_s, rec)
    assert 0.4 < rec["peak"] <= 0.55, rec
    # the tab stays audible: Chrome mutes a captured tab, so the offscreen document plays the stream back through a
    # pass-through meter to its AudioContext destination (the speaker itself: TLWU_PULSE_MONITOR, extension-capture.yml)
    assert st["playbackPeak"] > 0.4, st
    # the page itself kept playing in real time to the end while captured (not paused, stalled or muted)
    assert abs(page_state["t"] - CLIP_S) < 0.05 and not page_state["muted"], page_state
    assert CLIP_S - 0.2 <= played_s <= CLIP_S + 2.0, played_s
    assert stopped["ok"] is True and after["capturing"] is False, after
    assert badge_on == "ON" and badge_off == "", (badge_on, badge_off)
    if heard is not None:
        assert heard, "parec recorded nothing"
        # the captured tab is still audible on the real output device: the tone reached the speakers' sink
        secs = tone_seconds(heard)
        assert secs >= 0.9 * CLIP_S, (secs, len(heard) / 2 / MON_RATE)
