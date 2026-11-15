"""#974 speak smoke: the path a user takes after a clean install, without the window.

    tlwu.exe speak-smoke [--mine zh] [--other en] [--install-voice] [--clip zh.wav] [--json out.json]
    python -m tinglongwu.speaksmoke ...   (source layout)

Does what one press of 我來說 does in the GUI (App.toggle): quick_settings() -> the Kokoro voice offer (answered
"yes" with --install-voice: the same tlwu-setup --talk child the button starts) -> speak_route() on the VB-CABLE
"CABLE Input" -> settings_to_argv() -> run_session() with the GUI's SendGate (send mode "confirm"; this smoke presses
Send for every held line). Then it records "CABLE Output" (what Meet hears) and fails unless
  (1) no `voice_not_installed` notice, (2) the speak pair is MINE->OTHER from the microphone,
  (3) the recorded speech within WINDOW_S of the clip start has RMS > MIN_RMS_DBFS.
Test-only differences: the microphone is a zh clip (paced in real time) and the voice's capture check is told the
mic is a real one (the hosted runner's only microphone is "CABLE Output" itself). Exit 0 = PASS, 1 = FAIL.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import threading
import time
from pathlib import Path

import numpy as np

WINDOW_S = 30.0  # #974 Acceptance (4): voice on the speak sink within 30 s of the clip start
SPEECH_DBFS = -50.0  # a 20 ms frame is speech above this; same value as scripts/speak_selftest.py (#205)
MIN_RMS_DBFS = -35.0  # FAIL below this RMS over the speech frames; same as scripts/speak_selftest.py (#205)
RECORD = "CABLE Output"  # the meeting app's microphone when tlwu speaks into "CABLE Input" (#710)
CLIP_MIC = "speak-smoke clip"  # name the voice's capture check sees for the clip microphone
ZH_SENTENCES = ("今天天氣很好，我們去公園散步吧。", "請問會議幾點開始？", "這個問題我們明天再討論。")  # = scripts/ci_e2e.py
ZH_SPEAKER = 4  # zf_xiaobei (tts_worker KOKORO_ZH_VOICES[4]), the voice scripts/ci_e2e.py makes its zh clip with
VOICE_KEYS = ("voice_not_installed", "voice_same_device", "voice_device_missing", "voice_lang_unsupported")


def speak_start(mine: str, other: str, cable: str | None, settings: dict | None = None):
    """(run, hint, argv) exactly as App.toggle("speak") builds them (no voice offer, no window)."""
    from .gui.core import DEFAULTS, quick_settings, settings_to_argv, speak_route

    s = {**DEFAULTS, **(settings or {}), "target": mine, "lang": other}
    run = quick_settings(s, "speak")
    hint = speak_route(run, cable)
    return run, hint, settings_to_argv(run)


def pair_of(argv: list[str]) -> str:
    """'zh->en' from a CLI argv (--lang / --to)."""
    def val(flag):
        return argv[argv.index(flag) + 1] if flag in argv else "?"
    return f"{val('--lang')}->{val('--to')}"


def voice_keys(notices: list[str]) -> list[str]:
    """i18n keys of the voice-off lines among the GUI notices (the GUI shows t(key) as one red line)."""
    from .i18n import t

    texts = {t(k): k for k in VOICE_KEYS}
    return [texts[n] for n in notices if n in texts]


def speech_rms(x: np.ndarray, sr: int) -> tuple[float, float]:
    """(seconds of speech frames, RMS dBFS over those frames); 20 ms frames, as scripts/speak_selftest.py."""
    x = np.asarray(x, np.float64).reshape(-1)
    n = max(1, int(sr * 0.02))
    k = len(x) // n
    if k == 0:
        return 0.0, -120.0
    fr = x[: k * n].reshape(k, n)
    act = np.sqrt(np.mean(fr * fr, axis=1)) > 10 ** (SPEECH_DBFS / 20)
    sp = fr[act]
    rms = float(np.sqrt(np.mean(sp * sp))) if sp.size else 0.0
    return round(float(act.sum()) * 0.02, 2), round(20 * math.log10(rms), 1) if rms > 1e-9 else -120.0


def verdict(r: dict, mine: str, other: str) -> list[str]:
    """FAIL reasons of a report (empty = PASS)."""
    why = [f"voice off: {k}" for k in r.get("voice_off", [])]
    if r.get("pair") != f"{mine}->{other}":
        why.append(f"speak pair {r.get('pair')} != {mine}->{other}")
    if not r.get("mic"):
        why.append("speak mode does not capture the microphone")
    if not r.get("voice"):
        why.append(f"voice not on (hint {r.get('hint')})")
    if r.get("speech_s", 0) <= 0 or r.get("rms_dbfs", -120.0) <= MIN_RMS_DBFS:
        why.append(f"{RECORD}: speech {r.get('speech_s', 0)} s, RMS {r.get('rms_dbfs', -120.0)} dBFS "
                   f"<= {MIN_RMS_DBFS} within {WINDOW_S:.0f} s")
    if r.get("error"):
        why.append(r["error"])
    return why


def make_clip(py: str, model: str) -> tuple[np.ndarray, int]:
    """zh test speech from the installed Kokoro voice, through the app's own TTS worker (no wav in the repo)."""
    from .reverse.tts_client import TTSClient

    tts, parts, sr = TTSClient(py, model, "kokoro", ZH_SPEAKER, "zh"), [], 24000
    try:
        for text in ZH_SENTENCES:
            for h, data in tts.speak(text):
                if h["t"] == "chunk" and data:
                    sr = int(h.get("sr", sr))
                    parts.append(np.frombuffer(data, dtype=np.float32))
            parts.append(np.zeros(int(sr * 1.2), np.float32))
    finally:
        tts.close()
    return np.concatenate(parts).reshape(-1, 1), sr


def window_open(now: float, start_deadline: float, t_clip: list[float]) -> bool:
    """Keep the session going: before the mic opens until `start_deadline`; after it, WINDOW_S from the clip start."""
    return now < (t_clip[0] + WINDOW_S if t_clip else start_deadline)


class ClipMic:
    """The microphone for the smoke: the clip paced in real time, then silence until the window ends. `on_open` runs
    when the session starts reading (the recorder starts then); `t_clip` gets the start time."""

    def __init__(self, clip: np.ndarray, on_open, stop: threading.Event, t_clip: list, rate: int = 16000):
        self.clip, self.on_open, self.stop, self.t_clip, self.rate = clip, on_open, stop, t_clip, rate

    def frames(self):
        blk = self.rate // 2
        self.t_clip.append(time.monotonic())
        self.on_open()
        pad = np.concatenate([self.clip, np.zeros(int(WINDOW_S * self.rate), np.float32)])
        for i in range(0, len(pad), blk):
            if self.stop.is_set():
                return
            yield pad[i:i + blk]
            time.sleep(max(0.0, self.t_clip[0] + (i + blk) / self.rate - time.monotonic()))

    def close(self) -> None:  # pipeline.run() closes its source at the end (capture.base.AudioSource contract)
        self.stop.set()


class Recorder(threading.Thread):
    def __init__(self, name: str, sr: int = 48000):
        super().__init__(daemon=True)
        self.dev, self.sr, self.chunks, self.stop_ev, self.err = name, sr, [], threading.Event(), None

    def run(self):
        try:
            import soundcard as sc

            from .capture._soundcard import com_initialized, patch_mixformat

            patch_mixformat()
            with com_initialized():
                mic = next(m for m in sc.all_microphones() if self.dev.casefold() in m.name.casefold())
                # 2 channels, downmixed: soundcard documents corrupted single-channel WASAPI recordings
                with mic.recorder(samplerate=self.sr, channels=2, blocksize=self.sr // 10) as rec:
                    while not self.stop_ev.is_set():
                        self.chunks.append(rec.record(numframes=self.sr // 10).reshape(self.sr // 10, -1).mean(axis=1))
        except BaseException as e:  # noqa: BLE001
            self.err = f"{type(e).__name__}: {e}"


def run(a) -> dict:
    from . import capture, voice
    from .capture.base import SAMPLE_RATE, to_mono_16k
    from .cli import parse_args, run_session
    from .gui import voice_install
    from .gui.core import meeting_cable, run_voice_install, settings_to_argv, voice_outputs
    from .models import models_dir
    from .reverse.sendgate import SendGate

    mdir = models_dir()
    run_, hint, _ = speak_start(a.mine, a.other, meeting_cable())
    r: dict = {"hint": hint, "voice": bool(run_.get("voice")), "voice_device": run_.get("voice_device"),
               "voice_missing": voice_install.offer_needed(run_, mdir)}
    try:  # what the GUI would offer to download before this Start (#739 model, #673 pack); diagnostics only
        from .gui import pack_on_demand
        from .gui.core import missing_start_model

        r["model_missing"] = missing_start_model(run_)
        r["pack_missing"] = pack_on_demand.first_missing(run_.get("lang"), run_.get("target"), mdir)
    except Exception as e:  # noqa: BLE001
        r["offer_check"] = f"{type(e).__name__}: {e}"
    if r["voice_missing"] and a.install_voice:  # the "yes" answer of App._offer_voice
        r["voice_install_rc"] = run_voice_install(lambda ev: print("install", *ev[1:], flush=True))
    py, model = voice.kokoro_paths(mdir)
    import soundfile as sf

    try:
        data, sr = sf.read(a.clip, dtype="float32", always_2d=True) if a.clip else make_clip(py, model)
    except Exception as e:  # noqa: BLE001 - no Kokoro to make the clip = the voice is not installed either
        return {**r, "error": f"no zh clip: {type(e).__name__}: {e}"}
    clip = to_mono_16k(data, sr)
    notices: list[str] = []
    argv = settings_to_argv(run_, outputs=voice_outputs() if run_.get("voice") else None, notice=notices.append)
    r.update(pair=pair_of(argv), mic="--mic" in argv, argv=argv)
    args = parse_args(argv)
    gate = SendGate(lambda _t: None, time.monotonic, mode=run_.get("send_mode", "confirm"))
    args.voice_gate = gate
    rec, stop, t_clip = Recorder(RECORD), threading.Event(), []

    capture.MicSource = lambda *_a, **_k: ClipMic(clip, rec.start, stop, t_clip, SAMPLE_RATE)
    voice.resolve_capture = lambda file, mic, device: (CLIP_MIC, True)

    def worker():
        try:
            run_session(args, stop=stop, notice=notices.append, status=lambda m: print("status", m, flush=True))
        except Exception as e:  # noqa: BLE001
            r["error"] = f"run_session: {type(e).__name__}: {e}"

    th = threading.Thread(target=worker, daemon=True)
    th.start()
    end = time.monotonic() + a.timeout
    while th.is_alive() and window_open(time.monotonic(), end, t_clip):
        gate.tick()
        if gate.view().state == "asking":  # the user presses Send
            gate.confirm()
        time.sleep(0.1)
    if not t_clip and "error" not in r:
        r["error"] = f"the session never opened the microphone within {a.timeout:.0f} s"
    stop.set()
    rec.stop_ev.set()
    th.join(30)
    if rec.ident is not None:
        rec.join(5)
    audio = np.concatenate(rec.chunks) if rec.chunks else np.zeros(0, np.float32)
    r["speech_s"], r["rms_dbfs"] = speech_rms(audio[: int(WINDOW_S * rec.sr)], rec.sr)
    if rec.err and "error" not in r:
        r["error"] = f"recording {RECORD!r}: {rec.err}"
    r.update(notices=notices, voice_off=voice_keys(notices), sent=gate.sent)
    return r


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="tlwu speak-smoke", description=__doc__.split("\n")[0])
    ap.add_argument("--mine", default="zh", help="My language (default zh)")
    ap.add_argument("--other", default="en", help="Other side language (default en)")
    ap.add_argument("--install-voice", action="store_true", help="answer yes to the voice install offer")
    ap.add_argument("--clip", default=None, help="zh speech wav (default: made with the installed Kokoro voice)")
    ap.add_argument("--timeout", type=float, default=900.0, help="max seconds until the microphone opens")
    ap.add_argument("--json", default=None)
    a = ap.parse_args(argv)
    r = run(a)
    r["reasons"] = verdict(r, a.mine, a.other)
    r["verdict"] = "FAIL" if r["reasons"] else "PASS"
    text = json.dumps(r, ensure_ascii=False, indent=1)
    print(text, flush=True)
    if a.json:
        Path(a.json).write_text(text, "utf-8")
    print(f"speak-smoke {r['verdict']}" + (": " + "; ".join(r["reasons"]) if r["reasons"] else ""), flush=True)
    return 1 if r["reasons"] else 0


if __name__ == "__main__":
    sys.exit(main())
