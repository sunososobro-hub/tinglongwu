"""TTS worker: run with the tts-venv python (NOT the main venv).  Protocol over stdin/stdout pipes:

  stdin : JSON lines  {"id": n, "text": "..."}   |  {"cmd": "quit"}
  stdout: JSON header line, followed by `n` raw bytes when "n" is present:
          {"t":"ready","load_s":..,"vram_mib":..}
          {"t":"chunk","id":n,"sr":24000,"n":<bytes>,"ttfa_s":..}   + float32 mono PCM
          {"t":"done","id":n,"audio_s":..,"gen_s":..,"rtf":..,"ttfa_s":..}
          {"t":"error","id":n,"msg":".."}
Library prints are diverted to stderr so stdout carries only the protocol.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from pathlib import Path

# Same offline guard as the main process (the worker venv has no tinglongwu installed; load it from this checkout).
# `tinglongwu/__init__` only imports tinglongwu.offline (stdlib), so no extra packages are needed.
try:
    sys.path.append(str(Path(__file__).resolve().parents[2]))
    from tinglongwu import offline as _offline  # noqa: E402

    _offline.apply_env()
    _offline.set_mode("worker")
    _offline.install_guard()
except Exception as _e:  # noqa: BLE001  (layout changed: say so, do not break speech)
    print(f"tlwu worker: network guard NOT installed ({_e!r})", file=sys.stderr)

_OUT = os.fdopen(os.dup(1), "wb", buffering=0)
os.dup2(2, 1)  # anything printed by libraries goes to stderr
sys.stdout = sys.stderr


def send(obj: dict, payload: bytes = b"") -> None:
    if payload:
        obj = {**obj, "n": len(payload)}
    _OUT.write((json.dumps(obj) + "\n").encode("utf-8") + payload)


def run_vosk(a) -> int:
    """CPU path with Vosk-TTS (Apache-2.0, own G2P, no espeak). Fixed built-in voices; a request may carry
    "speaker" to switch voice on the fly. model_path is always local, so vosk never downloads anything."""
    import atexit
    import shutil
    import tempfile
    import wave

    import numpy as np
    from vosk_tts import Model, Synth

    t0 = time.perf_counter()
    synth = Synth(Model(model_path=a.model))
    # vosk's synth() only writes a wav file. The synthesized speech is the user's own translation, so it is
    # kept in a private (mkdtemp = owner-only) dir, deleted right after every read, and the dir removed on exit.
    tdir = tempfile.mkdtemp(prefix="tlwu_vosk_")
    atexit.register(shutil.rmtree, tdir, ignore_errors=True)
    tmp = os.path.join(tdir, "out.wav")
    send({"t": "ready", "load_s": round(time.perf_counter() - t0, 2), "vram_mib": 0, "device": "cpu",
          "engine": "vosk"})
    try:
        for line in sys.stdin:
            req = json.loads(line)
            if req.get("cmd") == "quit":
                break
            try:
                t1 = time.perf_counter()
                try:
                    synth.synth(req["text"], tmp, speaker_id=int(req.get("speaker", a.speaker)))
                    with wave.open(tmp, "rb") as w:
                        sr = w.getframerate()
                        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
                finally:
                    try:
                        os.remove(tmp)
                    except OSError:
                        pass
                arr = (pcm.astype(np.float32) / 32768.0).reshape(-1)
                gen = time.perf_counter() - t1
                dur = arr.size / sr
                send({"t": "chunk", "id": req["id"], "sr": sr, "ttfa_s": round(gen, 3)}, arr.tobytes())
                send({"t": "done", "id": req["id"], "audio_s": round(dur, 3), "gen_s": round(gen, 3),
                      "rtf": round(gen / dur, 3) if dur else None, "ttfa_s": round(gen, 3), "peak_vram_mib": 0})
            except Exception as e:  # noqa: BLE001
                send({"t": "error", "id": req.get("id"), "msg": repr(e)[:300]})
    finally:
        shutil.rmtree(tdir, ignore_errors=True)
    return 0


# Kokoro voice ids 0-4 (--speaker / v0-v4). Kept as a plain literal: tests read it with ast.
KOKORO_VOICES = ["am_michael", "af_heart", "am_adam", "af_bella", "bm_george"]
# Japanese voices for the same ids (best-graded first; Kokoro VOICES.md), used when a request has lang "ja".
KOKORO_JA_VOICES = ["jf_alpha", "jf_gongitsune", "jf_tebukuro", "jf_nezumi", "jm_kumo"]
# Mandarin voices for the same ids (lang "zh", #134; all eight zh voices of v1.0 share one grade in VOICES.md).
KOKORO_ZH_VOICES = ["zf_xiaoxiao", "zm_yunxi", "zf_xiaoyi", "zm_yunjian", "zf_xiaobei"]
KOKORO_MODEL_FILE = "kokoro-v1.0.int8.onnx"
KOKORO_VOICES_FILE = "voices-v1.0.bin"


def kokoro_voice(speaker, available=None, warn=lambda m: print(m, file=sys.stderr, flush=True),
                 voices=None) -> str:
    """Voice name for speaker id 0-4 (`voices` = KOKORO_VOICES or KOKORO_JA_VOICES); unknown id or a name missing
    from `available` falls back to the first voice of that list."""
    voices = voices or KOKORO_VOICES
    default = voices[0]
    try:
        name = voices[int(speaker)]
        if int(speaker) < 0:
            raise IndexError
    except (IndexError, ValueError, TypeError):
        warn(f"kokoro: unknown speaker {speaker!r}, using {default}")
        return default
    if available is not None and name not in available:
        warn(f"kokoro: voice {name} not in voices file, using {default}")
        return default
    return name


class JaPhonemizer:
    """Japanese text -> Kokoro phonemes with misaki[ja] (pyopenjtalk + fugashi/unidic). Loaded on the first
    Japanese request only. espeak's own "ja" is unusable (it spells kanji out), so it is never used for ja."""

    def __init__(self):
        self._g2p = None

    def __call__(self, text: str) -> str:
        if self._g2p is None:
            from misaki import ja

            self._g2p = ja.JAG2P()
        return self._g2p(text)[0]


class ZhPhonemizer:
    """Chinese text (Traditional is fine: misaki converts to pinyin via pypinyin/jieba) -> Kokoro v1.0 phonemes
    with misaki[zh]. Loaded on the first Chinese request only; espeak's cmn voice is never used."""

    def __init__(self):
        self._g2p = None

    def __call__(self, text: str) -> str:
        if self._g2p is None:
            from misaki import zh

            self._g2p = zh.ZHG2P()
        return self._g2p(text)[0]


def kokoro_request(text: str, lang: str | None, phonemizers: dict) -> tuple[list, dict]:
    """(voice list, k.create kwargs) for one request: ja/zh go through misaki phonemes, anything else is en-us."""
    if lang in ("ja", "zh"):
        voices = KOKORO_JA_VOICES if lang == "ja" else KOKORO_ZH_VOICES
        return voices, dict(text=phonemizers[lang](text), is_phonemes=True)
    return KOKORO_VOICES, dict(text=text, lang="en-us")


def run_kokoro(a, ja_phonemizer=None, zh_phonemizer=None) -> int:
    """CPU path with kokoro-onnx (English, Japanese, Chinese). It bundles GPL espeak, so it only ever runs here, in its own venv.
    Needs kokoro-v1.0.int8.onnx + voices-v1.0.bin in --model; a request may carry "speaker" (0-4)."""
    import numpy as np
    from kokoro_onnx import Kokoro

    mdir = os.path.abspath(a.model)
    onnx, voices = os.path.join(mdir, KOKORO_MODEL_FILE), os.path.join(mdir, KOKORO_VOICES_FILE)
    for f in (onnx, voices):
        if not os.path.isfile(f):
            send({"t": "error", "id": None, "msg": f"missing local file: {f}"})
            return 2
    t0 = time.perf_counter()
    k = Kokoro(onnx, voices)
    phonemizers = {"ja": ja_phonemizer or JaPhonemizer(), "zh": zh_phonemizer or ZhPhonemizer()}
    try:
        avail = set(k.get_voices())
    except Exception:  # noqa: BLE001  (API not present: handle failures per request instead)
        avail = None
    send({"t": "ready", "load_s": round(time.perf_counter() - t0, 2), "vram_mib": 0, "device": "cpu",
          "engine": "kokoro"})
    for line in sys.stdin:
        req = json.loads(line)
        if req.get("cmd") == "quit":
            break
        try:
            t1 = time.perf_counter()
            voices, kw = kokoro_request(req["text"], req.get("lang"), phonemizers)
            voice = kokoro_voice(req.get("speaker", a.speaker), avail, voices=voices)
            try:
                samples, sr = k.create(voice=voice, speed=1.0, **kw)
            except (KeyError, ValueError, AssertionError) as e:
                if voice == voices[0]:
                    raise
                print(f"kokoro: voice {voice} failed ({e!r}), using {voices[0]}", file=sys.stderr, flush=True)
                samples, sr = k.create(voice=voices[0], speed=1.0, **kw)
            arr = np.asarray(samples, dtype=np.float32).reshape(-1)
            gen = time.perf_counter() - t1
            dur = arr.size / sr
            send({"t": "chunk", "id": req["id"], "sr": int(sr), "ttfa_s": round(gen, 3)}, arr.tobytes())
            send({"t": "done", "id": req["id"], "audio_s": round(dur, 3), "gen_s": round(gen, 3),
                  "rtf": round(gen / dur, 3) if dur else None, "ttfa_s": round(gen, 3), "peak_vram_mib": 0})
        except Exception as e:  # noqa: BLE001
            send({"t": "error", "id": req.get("id"), "msg": repr(e)[:300]})
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", default="vosk", choices=["vosk", "kokoro"])
    ap.add_argument("--speaker", type=int, default=4,
                    help="vosk: built-in voice id (ru 0.7: 0-4); kokoro: 0-4 (default there: 0)")
    ap.add_argument("--model", required=True, help="local Vosk-TTS model dir / Kokoro model dir")
    a = ap.parse_args()
    # Ctrl+C in the terminal reaches this child too (same process group): it would die in `for line in sys.stdin`
    # with a traceback. The parent ends us (quit command / stdin EOF / kill), so ignore the signal.
    try:
        signal.signal(signal.SIGINT, signal.SIG_IGN)
    except (ValueError, OSError):  # pragma: no cover - not the main thread / unsupported
        pass
    os.environ["HF_HUB_OFFLINE"] = "1"
    if a.engine == "kokoro":
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        return run_kokoro(a)
    return run_vosk(a)


if __name__ == "__main__":
    raise SystemExit(main())
