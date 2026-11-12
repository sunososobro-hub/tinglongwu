#!/usr/bin/env python
"""#205 speak-mode self-test: feed a fixed Chinese clip into the real speak pipeline (VAD -> Whisper -> MT -> TTS ->
player), record what lands on the virtual mic, and score completeness. Exit 0 = PASS, 1 = FAIL / could not run.

    python scripts/speak_selftest.py --clip clip.wav [--to en] [--json out.json] [--keep-wav rec.wav]

How it works (Windows: VB-CABLE must be installed; Linux: PulseAudio/PipeWire, records the tlwu_mic virtual mic): the same `tinglongwu.reverse.proto` live path runs in a child
process, with only the microphone replaced by the clip (paced in real time, so VAD / endpointing behave as with a
voice). Its output goes to "CABLE Input" (what windows_menu.ps1 uses); this process records the other end, "CABLE
Output", with soundcard. Test-only differences from a real session: the half-duplex playback gate is off (the clip
must not be dropped while our own TTS plays) and the mic feedback guard is skipped (the clip is not a mic).
Nothing is played through speakers and the clip is never written anywhere. Transcripts are not put in the JSON.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # this checkout, not an installed/editable copy elsewhere
FRAME_S = 0.02
SPEECH_DBFS = -50.0   # a 20 ms frame counts as speech above this RMS
MIN_RECORDED_RATIO = 0.90   # FAIL if recorded speech < 90 % of TTS-produced speech
MIN_RMS_DBFS = -35.0        # FAIL if RMS of the recorded speech < -35 dBFS
MAX_STREAM_GAP_S = 0.30     # #207: FAIL if streamed TTS pieces of one sentence leave a silence longer than this
TAIL_S = 2.0                # keep recording this long after the pipeline is done
CABLE_IN, CABLE_OUT = "CABLE Input", "CABLE Output"
LINUX_MIC = "tlwu_mic"  # Linux: the pipeline creates tlwu_sink + tlwu_mic itself (vmic.py); we record tlwu_mic


def record_name(platform: str | None = None) -> str:
    return CABLE_OUT if (platform or sys.platform) == "win32" else LINUX_MIC


def dbfs(x: float) -> float:
    return 20 * math.log10(x) if x > 1e-9 else -120.0


def speech_stats(x: np.ndarray, sr: int) -> dict:
    """speech_s: seconds of 20 ms frames above SPEECH_DBFS; rms_dbfs: RMS over those frames only (silence between
    sentences must not dilute the level); rms_all_dbfs: RMS over everything; peak_dbfs: sample peak."""
    x = np.asarray(x, dtype=np.float64).reshape(-1)
    n = max(1, int(sr * FRAME_S))
    k = len(x) // n
    if k == 0:
        return {"seconds": 0.0, "speech_s": 0.0, "rms_dbfs": -120.0, "rms_all_dbfs": -120.0, "peak_dbfs": -120.0}
    fr = x[: k * n].reshape(k, n)
    rms = np.sqrt(np.mean(fr * fr, axis=1))
    act = rms > 10 ** (SPEECH_DBFS / 20)
    speech = fr[act]
    return {"seconds": round(len(x) / sr, 2), "speech_s": round(float(act.sum()) * FRAME_S, 2),
            "rms_dbfs": round(dbfs(float(np.sqrt(np.mean(speech * speech)))) if speech.size else -120.0, 1),
            "rms_all_dbfs": round(dbfs(float(np.sqrt(np.mean(x * x)))), 1),
            "peak_dbfs": round(dbfs(float(np.max(np.abs(x)))), 1)}


def score(tts_speech_s: float, rec: dict) -> dict:
    """PASS/FAIL (#205): FAIL if recorded speech < 90 % of the TTS-produced speech or RMS < -35 dBFS."""
    ratio = rec["speech_s"] / tts_speech_s if tts_speech_s > 0 else 0.0
    why = []
    if tts_speech_s <= 0:
        why.append("TTS produced no speech")
    if ratio < MIN_RECORDED_RATIO:
        why.append(f"recorded speech {ratio * 100:.0f}% of TTS < {MIN_RECORDED_RATIO * 100:.0f}%")
    if rec["rms_dbfs"] < MIN_RMS_DBFS:
        why.append(f"RMS {rec['rms_dbfs']} dBFS < {MIN_RMS_DBFS}")
    return {"verdict": "FAIL" if why else "PASS", "recorded_ratio": round(ratio, 3), "reasons": why}


def score_wavs(tts_wav: str, rec_wav: str) -> dict:
    """Score a recorded wav against the wav of what the TTS produced (used by the unit test and for re-scoring)."""
    import soundfile as sf

    t, tsr = sf.read(tts_wav, dtype="float32", always_2d=True)
    r, rsr = sf.read(rec_wav, dtype="float32", always_2d=True)
    ts, rs = speech_stats(t.mean(axis=1), tsr), speech_stats(r.mean(axis=1), rsr)
    return {**score(ts["speech_s"], rs), "tts": ts, "recorded": rs}


# "tts_first" since #207 (plain "tts" before): MT done -> first TTS audio
TIMING = re.compile(r"\[vad ([\d.-]+) \| stt ([\d.-]+) \| mt ([\d.-]+) \| tts(?:_first)? ([\d.-]+) \| total ([\d.]+) s\]")
LATENCY = re.compile(r"latency asr=\S+ mt=\S+ first_audio=(\S+)s done=\S+")
STREAM = re.compile(r"stream pieces=(\d+) gap_max=([\d.]+)s rtf=(\S+)")


def _num(s: str):
    return None if s == "-" else float(s)


def parse_log(text: str) -> dict:
    lat = [dict(zip(("vad", "stt", "mt", "tts", "total"), map(_num, m.groups()))) for m in TIMING.finditer(text)]
    spoken = sum(1 for m in LATENCY.finditer(text) if m.group(1) != "-")
    streams = [{"pieces": int(m.group(1)), "gap_max": float(m.group(2)), "rtf": _num(m.group(3))}
               for m in STREAM.finditer(text)]
    return {"latencies": lat, "sentences_tts": max(spoken, sum(1 for d in lat if d["tts"] is not None)),
            "streams": streams}


def stage_table(latencies: list[dict]) -> dict:
    """#207: {stage: {"p50": s, "max": s}} over the per-sentence [vad|stt|mt|tts|total] lines (stages that ran)."""
    out = {}
    for k in ("vad", "stt", "mt", "tts", "total"):
        v = sorted(d[k] for d in latencies if d.get(k) is not None)
        if v:
            out[k] = {"p50": round(float(np.median(v)), 2), "max": round(v[-1], 2)}
    return out


def stream_gap_reason(streams: list[dict]):
    """#207: FAIL reason when a streamed sentence had a silence > MAX_STREAM_GAP_S between its pieces, else None."""
    worst = max((d["gap_max"] for d in streams), default=0.0)
    return f"stream gap {worst:.2f}s > {MAX_STREAM_GAP_S:.2f}s" if worst > MAX_STREAM_GAP_S else None


# ---------------------------------------------------------------- child: the real speak pipeline with the clip as mic
def _child(a) -> int:
    sys.path.insert(0, str(ROOT))
    import soundfile as sf

    from tinglongwu.capture import mic as capmic
    from tinglongwu.capture.base import SAMPLE_RATE, to_mono_16k
    from tinglongwu.reverse import live, proto

    data, sr = sf.read(a.clip, dtype="float32", always_2d=True)
    clip = to_mono_16k(data, sr)
    segs = [0]
    pushed: dict[int, list] = {}
    players: list = []

    class ClipSource:
        def __init__(self, *_a, **_k):
            pass

        def frames(self):
            blk = SAMPLE_RATE // 2
            t0 = time.monotonic()
            padded = np.concatenate([clip, np.zeros(int(3.0 * SAMPLE_RATE), np.float32)])
            for i in range(0, len(padded), blk):
                yield padded[i:i + blk]
                time.sleep(max(0.0, t0 + (i + blk) / SAMPLE_RATE - time.monotonic()))

    capmic.MicSource = ClipSource
    live.check_mic_feedback = lambda *x, **k: None
    live.PlaybackGate.busy = lambda self: False
    orig_cap = live.capture_segments

    def counting(*x, **k):
        for s in orig_cap(*x, **k):
            segs[0] += 1
            yield s

    live.capture_segments = counting
    orig_push, orig_init = proto.Player.push, proto.Player.__init__

    def push(self, audio, sr):
        if self.enabled:
            pushed.setdefault(int(sr), []).append(np.asarray(audio, np.float32))
        return orig_push(self, audio, sr)

    def init(self, *x, **k):
        orig_init(self, *x, **k)
        players.append(self)

    proto.Player.push, proto.Player.__init__ = push, init
    rc = proto.main(a.proto_args)
    for p in players:  # proto stops the player on exit; let the queued audio finish playing first
        t0 = time.monotonic()
        while (p.gate._n > 0 or not p.q.empty()) and time.monotonic() - t0 < 120:
            time.sleep(0.1)
    time.sleep(1.0)
    ts = [speech_stats(np.concatenate(v), r) for r, v in pushed.items()]
    print("SELFTEST " + json.dumps({"segments": segs[0], "tts_s": round(sum(t["seconds"] for t in ts), 2),
                                    "tts_speech_s": round(sum(t["speech_s"] for t in ts), 2), "rc": rc}), flush=True)
    return 0


# ---------------------------------------------------------------- parent: record the cable, run the child, score
class CableRecorder(threading.Thread):
    def __init__(self, name: str, sr: int = 48000):
        super().__init__(daemon=True)
        self.name, self.sr, self.chunks, self.stop_ev, self.err = name, sr, [], threading.Event(), None

    def run(self):
        try:
            import soundcard as sc

            def loop():
                mic = next(m for m in sc.all_microphones(include_loopback=sys.platform != "win32")
                           if self.name.casefold() in m.name.casefold())
                with mic.recorder(samplerate=self.sr, channels=1, blocksize=self.sr // 10) as rec:
                    while not self.stop_ev.is_set():
                        self.chunks.append(rec.record(numframes=self.sr // 10).reshape(-1))

            if sys.platform == "win32":
                from tinglongwu.capture._soundcard import com_initialized, patch_mixformat

                patch_mixformat()
                with com_initialized():
                    loop()
            else:
                loop()
        except BaseException as e:  # noqa: BLE001
            self.err = f"{type(e).__name__}: {e}"

    def audio(self) -> np.ndarray:
        return np.concatenate(self.chunks).astype(np.float32) if self.chunks else np.zeros(0, np.float32)


def _find(*cands: Path):
    return next((str(c) for c in cands if c.exists()), None)


def _proto_args(a, platform: str | None = None) -> list[str]:
    win = (platform or sys.platform) == "win32"
    home = Path.home()
    kpy = a.tts_python or _find(*([home / "tts-kokoro" / "Scripts" / "python.exe"] if win else
                                  [home / "tts-kokoro" / "bin" / "python"])) or ""
    kmodel = a.tts_model or _find(Path(a.models_dir or ROOT / "models") / "kokoro", home / "models" / "kokoro") or ""
    args = ["--mic", "--to", a.to, "--no-confirm"]
    if win:
        args += ["--device", CABLE_IN, "--allow-monitor"]  # Linux: no --device, proto creates tlwu_sink/tlwu_mic
    if a.models_dir:
        args += ["--models-dir", a.models_dir]
    if a.to in ("en", "ja"):
        args += ["--tts-engine", "kokoro", "--tts-python", kpy, "--tts-model", kmodel, "--speaker", "0"]
    args += ["--mt", a.mt]
    return args + (["--whisper", a.whisper] if a.whisper else [])


def run(a) -> dict:
    out: dict = {"clip": Path(a.clip).name, "to": a.to}
    rec = CableRecorder(record_name())
    cmd = [sys.executable, str(Path(__file__).resolve()), "--child", "--clip", a.clip, "--"] + _proto_args(a)
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "PYTHONPATH": str(ROOT)}
    lines: list[str] = []
    p = subprocess.Popen(cmd, cwd=str(ROOT), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    def pump():
        for raw in p.stdout:
            line = raw.decode("utf-8", "replace").rstrip()
            lines.append(line)
            if line.startswith("listening") and not rec.is_alive():
                rec.start()  # models are loaded; the clip starts now

    th = threading.Thread(target=pump, daemon=True)
    th.start()
    try:
        p.wait(timeout=a.timeout)
    except subprocess.TimeoutExpired:
        p.kill()
        out["error"] = f"timeout after {a.timeout}s"
    th.join(5)
    time.sleep(TAIL_S)
    rec.stop_ev.set()
    rec.join(5)
    text = "\n".join(lines)
    if a.keep_log:
        Path(a.keep_log).write_text(text, encoding="utf-8")
    m = re.search(r"^SELFTEST (\{.*\})$", text, re.M)
    child = json.loads(m.group(1)) if m else {}
    if rec.err:
        out["error"] = f"recording {rec.name!r} failed: {rec.err}"
    elif not m and "error" not in out:
        out["error"] = "speak pipeline did not finish: " + " | ".join(lines[-3:])[:300]
    audio = rec.audio()
    if a.keep_wav and len(audio):
        import soundfile as sf

        sf.write(a.keep_wav, audio, rec.sr)
    rs = speech_stats(audio, rec.sr)
    log = parse_log(text)
    tts_speech = float(child.get("tts_speech_s", 0.0))
    res = score(tts_speech, rs)
    if "error" in out:
        res = {**res, "verdict": "FAIL", "reasons": [out["error"]] + res["reasons"]}
    gap = stream_gap_reason(log["streams"])
    if gap:
        res = {**res, "verdict": "FAIL", "reasons": res["reasons"] + [gap]}
    out.update({"sentences_in": child.get("segments", 0), "sentences_tts": log["sentences_tts"],
                "expected_tts_speech_s": tts_speech, "tts_audio_s": child.get("tts_s", 0.0),
                "recorded_speech_s": rs["speech_s"], "recorded_ratio": res["recorded_ratio"],
                "peak_dbfs": rs["peak_dbfs"], "rms_dbfs": rs["rms_dbfs"], "rms_all_dbfs": rs["rms_all_dbfs"],
                "latencies": log["latencies"], "stages": stage_table(log["latencies"]),
                "stream_gap_max_s": max((d["gap_max"] for d in log["streams"]), default=0.0),
                "tts_rtf": [d["rtf"] for d in log["streams"]], "verdict": res["verdict"], "reasons": res["reasons"]})
    return out


def summary(o: dict) -> str:
    return (f"{o['verdict']} in={o['sentences_in']} tts={o['sentences_tts']} "
            f"recorded={o['recorded_speech_s']}s/{o['expected_tts_speech_s']}s ({o['recorded_ratio'] * 100:.0f}%) "
            f"rms={o['rms_dbfs']}dBFS peak={o['peak_dbfs']}dBFS"
            + "".join(f" {k}_p50={v['p50']}s" for k, v in o.get("stages", {}).items() if k in ("tts", "total"))
            + f" gap_max={o.get('stream_gap_max_s', 0.0)}s"
            + (" reasons: " + "; ".join(o["reasons"]) if o["reasons"] else ""))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="speak-mode self-test: clip -> speak pipeline -> virtual mic recording")
    ap.add_argument("--clip", required=True, help="Chinese test clip (wav/flac/mp3, ~20-40 s); reuse the same one each run")
    ap.add_argument("--to", default="en", choices=["en", "ru", "ja"])
    ap.add_argument("--mt", default="auto", choices=["auto", "hymt"])
    ap.add_argument("--whisper", default=None)
    ap.add_argument("--models-dir", default=None)
    ap.add_argument("--tts-python", default=None, help="default: ~/tts-kokoro/Scripts/python.exe (Windows) or ~/tts-kokoro/bin/python (Linux)")
    ap.add_argument("--tts-model", default=None, help="default: <models>/kokoro")
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--json", default=None, metavar="PATH", help="also write the JSON here")
    ap.add_argument("--keep-wav", default=None, metavar="PATH", help="save the recorded virtual-mic audio")
    ap.add_argument("--keep-log", default=None, metavar="PATH", help="save the speak pipeline console log (has transcripts)")
    ap.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("proto_args", nargs="*", help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    if a.child:
        return _child(a)
    if sys.platform not in ("win32", "linux"):
        print("FAIL speak_selftest supports Windows (VB-CABLE) and Linux (PulseAudio/PipeWire) only", file=sys.stderr)
        return 1
    if not Path(a.clip).is_file():
        print(f"FAIL clip not found: {a.clip}", file=sys.stderr)
        return 1
    o = run(a)
    js = json.dumps(o, ensure_ascii=False, indent=1)
    if a.json:
        Path(a.json).write_text(js, encoding="utf-8")
    print(js)
    print(summary(o))
    return 0 if o["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
