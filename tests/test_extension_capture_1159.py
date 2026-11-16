"""#1159 (Chrome extension part 2, parent #711): the unpacked MV3 extension in extension/capture captures a tab's
audio with chrome.tabCapture, keeps the tab audible and hands 16 kHz mono frames to the recognizer hook.

The capture test loads the extension in Playwright's Chromium, plays a 3 s 440 Hz WAV in a local page and reads the
recognizer hook's counters from the offscreen document. CI runs it in the `test` job with TLWU_REQUIRE_EXTENSION=1
(a skip there is a failure); elsewhere it skips when Playwright / Chromium / node are missing.

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
    assert sorted(m["permissions"]) == ["offscreen", "tabCapture"]
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
        try:
            page = ctx.new_page()
            page.goto(tone_page)
            page.wait_for_function("document.getElementById('a').readyState >= 4")
            started = sw.evaluate("""async () => {
                const [t] = await chrome.tabs.query({active: true, lastFocusedWindow: true});
                return await tlwu.startCapture(t.id);
            }""")
            assert started["ok"] is True, started
            page.click("#play")  # a real click: the default autoplay policy, as for a user
            t0 = time.monotonic()
            page.wait_for_function("document.getElementById('a').ended", timeout=20000)
            played_s = time.monotonic() - t0
            page_state = page.evaluate("(() => { const a = document.getElementById('a');"
                                       " return {t: a.currentTime, d: a.duration, muted: a.muted}; })()")
            time.sleep(0.5)  # the last frames are still in the worklet / message queue
            st = sw.evaluate("() => tlwu.captureStats()")
            stopped = sw.evaluate("() => tlwu.stopCapture()")
            after = sw.evaluate("() => tlwu.captureStats()")
        finally:
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
    # pass-through meter to its AudioContext destination (a headless runner has no speaker to listen to: owner check)
    assert st["playbackPeak"] > 0.4, st
    # the page itself kept playing in real time to the end while captured (not paused, stalled or muted)
    assert abs(page_state["t"] - CLIP_S) < 0.05 and not page_state["muted"], page_state
    assert CLIP_S - 0.2 <= played_s <= CLIP_S + 2.0, played_s
    assert stopped["ok"] is True and after["capturing"] is False, after
