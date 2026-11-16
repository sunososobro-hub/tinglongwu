#!/usr/bin/env python
"""#1157: owner hand-tests that the owner-PC hw-tests runner checks on every run (see .github/workflows/hw-tests.yml).

    python scripts/hw_owner_checks.py gpu --doctor-log doctor.txt        #966: auto -> cuda, no "update your driver"
    python scripts/hw_owner_checks.py kokoro --models-dir M --tts-python P   #967: voice files + Kokoro venv present
    python scripts/hw_owner_checks.py noise-clip OUT.wav                  #968: the keyboard-noise clip to feed
    python scripts/hw_owner_checks.py noise --noise-json n.json --speech-json s.json   #968: 0 sent from noise, >= 1 from zh

Each subcommand prints one `PASS ...` / `FAIL ...` line (the hw-tests step summary shows it) and exits 0 / 1.
`gpu` and `kokoro` run inside the python of the setup under test (the fresh temp-dir setup for #966 / #967), so they
see what a user's fresh install sees, not the runner's kept venv.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# #966: advice that sends an NVIDIA user to the driver while the real cause can be missing cuBLAS / cuDNN: the v0.3.5
# text ("update your NVIDIA graphics driver") and gpu_driver_old in every UI language (en / zh / ru / ja). The GPU-pack
# message says the opposite ("Updating the driver does not help", "更新驅動程式沒有幫助"), which must not match.
DRIVER_ADVICE = re.compile(r"\bupdate (your|the) (nvidia )?(graphics )?driver|請更新驅動程式|Обновите драйвер|"
                           r"ドライバーを更新してください", re.I)
NOISE_CLIP = ROOT / "tests" / "data" / "keyboard_noise_968.wav"  # a recorded clip, when the owner adds one
NOISE_SECONDS = 20.0


def driver_advice_lines(text: str) -> list[str]:
    """Lines of `text` that tell the user to update the NVIDIA driver."""
    return [ln.strip() for ln in text.splitlines() if DRIVER_ADVICE.search(ln)]


def check_gpu(device: str, warnings: list[str], doctor_text: str) -> tuple[bool, str]:
    bad = driver_advice_lines("\n".join(warnings) + "\n" + doctor_text)
    if device != "cuda":
        return False, f"resolve_device('auto') = {device!r}, not 'cuda'" + (f" ({warnings[0]})" if warnings else "")
    if bad:
        return False, f"driver advice shown on a working GPU: {bad[0]!r}"
    return True, "resolve_device('auto') = 'cuda', no 'update your driver' line"


def kokoro_missing(model_dir: Path, tts_python: Path | None, run=subprocess.call) -> list[str]:
    """What a speak session would miss: the pinned Kokoro files (downloader.KOKORO_FILES) and a Kokoro venv that
    imports kokoro_onnx."""
    from tinglongwu_setup.downloader import KOKORO_FILES

    miss = [n for n in KOKORO_FILES if not (model_dir / n).is_file() or (model_dir / n).stat().st_size == 0]
    if tts_python is not None:
        if not tts_python.is_file():
            miss.append(f"Kokoro venv python {tts_python}")
        elif run([str(tts_python), "-c", "import kokoro_onnx, soundfile"]) != 0:
            miss.append("kokoro_onnx does not import in the Kokoro venv")
    return miss


def noise_audio(seconds: float = NOISE_SECONDS):
    """(audio float32 mono, sample rate, source). The recorded clip under tests/data when present, else the seeded synthetic
    keyboard clip of tlwu_eval.speak_noise (the one the #968 gate thresholds were measured on)."""
    if NOISE_CLIP.is_file():
        import soundfile as sf

        x, sr = sf.read(str(NOISE_CLIP), dtype="float32", always_2d=True)
        return x.mean(axis=1), sr, f"recorded {NOISE_CLIP.relative_to(ROOT).as_posix()}"
    from tlwu_eval.speak_noise import SR, synth

    return synth("keyboard", seconds), SR, f"synthetic keyboard {seconds:.0f} s (tlwu_eval.speak_noise, seed 968)"


def check_noise(noise: dict, speech: dict) -> tuple[bool, str]:
    """speak_selftest JSONs: the noise clip must send 0 sentences (no TTS line), the zh speech clip >= 1. A noise
    run that did not finish proves nothing, so it fails instead of counting as 0."""
    reasons = []
    if noise.get("error"):
        reasons.append(f"noise run did not finish: {noise['error']}")
    elif noise.get("sentences_tts", 0) != 0:
        reasons.append(f"noise sent {noise['sentences_tts']} sentence(s)")
    if speech.get("error"):
        reasons.append(f"speech run did not finish: {speech['error']}")
    elif speech.get("sentences_tts", 0) < 1:
        reasons.append("zh speech sent 0 sentences")
    msg = (f"noise sent={noise.get('sentences_tts', '?')} (segments in={noise.get('sentences_in', '?')}), "
           f"zh speech sent={speech.get('sentences_tts', '?')}")
    return (not reasons), msg + ("; " + "; ".join(reasons) if reasons else "")


def _report(ok: bool, msg: str, what: str) -> int:
    print(f"{'PASS' if ok else 'FAIL'} {what}: {msg}")
    return 0 if ok else 1


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("gpu")
    g.add_argument("--doctor-log", required=True, help="`tlwu doctor` output of the same setup")
    k = sub.add_parser("kokoro")
    k.add_argument("--models-dir", required=True)
    k.add_argument("--tts-python", default=None)
    c = sub.add_parser("noise-clip")
    c.add_argument("out")
    c.add_argument("--seconds", type=float, default=NOISE_SECONDS)
    n = sub.add_parser("noise")
    n.add_argument("--noise-json", required=True)
    n.add_argument("--speech-json", required=True)
    a = p.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if a.cmd == "gpu":
        from tinglongwu.device import resolve_device

        warns: list[str] = []
        dev = resolve_device("auto", warn=warns.append)
        text = Path(a.doctor_log).read_text("utf-8", "replace")
        return _report(*check_gpu(dev, warns, text), what="#966 GPU after fresh setup")
    if a.cmd == "kokoro":
        miss = kokoro_missing(Path(a.models_dir) / "kokoro", Path(a.tts_python) if a.tts_python else None)
        return _report(not miss, "missing: " + ", ".join(miss) if miss else "model files and Kokoro venv present",
                       what="#967 Kokoro voice after fresh setup")
    if a.cmd == "noise-clip":
        import soundfile as sf

        x, sr, src = noise_audio(a.seconds)
        sf.write(a.out, x, sr)
        print(f"noise clip: {src} -> {a.out}")
        return 0
    noise = json.loads(Path(a.noise_json).read_text("utf-8"))
    speech = json.loads(Path(a.speech_json).read_text("utf-8"))
    return _report(*check_noise(noise, speech), what="#968 keyboard noise through the speak pipeline")


if __name__ == "__main__":
    sys.exit(main())
