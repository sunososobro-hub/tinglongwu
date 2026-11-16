"""#1157: the owner-PC hw-tests checks for #966 / #967 / #968 (scripts/hw_owner_checks.py), run on a Linux runner."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from tinglongwu.i18n import MESSAGES

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "hw_owner_checks.py"


def _load():
    spec = importlib.util.spec_from_file_location("hw_owner_checks", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


hc = _load()
V035 = ("tlwu: CUDA device found but cublas64_12.dll, cudnn64_9.dll missing; falling back to CPU. To use the GPU, "
        "update your NVIDIA graphics driver (CUDA 12 or newer)")


def _cli(*args, cwd=None):
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, encoding="utf-8",
                          cwd=cwd or ROOT, timeout=120)


@pytest.mark.parametrize("lang", ["en", "zh-TW", "ru", "ja"])
def test_driver_advice_flags_old_driver_text_but_not_the_gpu_pack_message(lang):
    table = MESSAGES[lang]
    old = table["gpu_driver_old"].format(have="457.49", need="525.60", url="https://example.invalid")
    pack = table["gpu_pack_missing"].format(libs="cublas64_12.dll")
    assert hc.driver_advice_lines(old) == [old]
    assert hc.driver_advice_lines(pack) == []  # "updating the driver does not help" is the right advice


def test_check_gpu_verdicts():
    assert hc.check_gpu("cuda", [], "[  ok] GPU: NVIDIA GeForce RTX 4060 (8188 MiB)\n")[0] is True
    ok, msg = hc.check_gpu("cpu", [V035], "")
    assert not ok and "'cpu'" in msg
    ok, msg = hc.check_gpu("cuda", [], "[warn] GPU\n  fix: " + V035)
    assert not ok and "update your NVIDIA graphics driver" in msg


def test_kokoro_missing_lists_files_and_venv(tmp_path):
    from tinglongwu_setup.downloader import KOKORO_FILES

    kdir = tmp_path / "kokoro"
    kdir.mkdir()
    assert sorted(hc.kokoro_missing(kdir, None)) == sorted(KOKORO_FILES)
    for n in KOKORO_FILES:
        (kdir / n).write_bytes(b"x")
    assert hc.kokoro_missing(kdir, None) == []
    assert hc.kokoro_missing(kdir, tmp_path / "nope" / "python.exe")[0].startswith("Kokoro venv python")
    py = tmp_path / "python.exe"
    py.write_bytes(b"")
    assert hc.kokoro_missing(kdir, py, run=lambda cmd: 1) == ["kokoro_onnx does not import in the Kokoro venv"]
    assert hc.kokoro_missing(kdir, py, run=lambda cmd: 0) == []


def test_kokoro_cli_fails_on_empty_models_dir(tmp_path):
    r = _cli("kokoro", "--models-dir", str(tmp_path))
    assert r.returncode == 1 and r.stdout.startswith("FAIL #967") and "voices-v1.0.bin" in r.stdout


def test_noise_clip_cli_writes_a_20s_keyboard_wav(tmp_path):
    import soundfile as sf

    out = tmp_path / "kb.wav"
    r = _cli("noise-clip", str(out))
    assert r.returncode == 0, r.stderr
    x, sr = sf.read(str(out))
    assert sr == 16000 and abs(len(x) / sr - 20.0) < 0.01 and x.std() > 0.001


@pytest.mark.parametrize("noise,speech,ok", [
    ({"sentences_in": 3, "sentences_tts": 0}, {"sentences_in": 4, "sentences_tts": 4}, True),
    ({"sentences_in": 3, "sentences_tts": 2}, {"sentences_in": 4, "sentences_tts": 4}, False),  # hallucination sent
    ({"sentences_in": 0, "sentences_tts": 0}, {"sentences_in": 4, "sentences_tts": 0}, False),  # gate drops speech
    ({"sentences_tts": 0, "error": "timeout after 900s"}, {"sentences_tts": 4}, False),  # unfinished != 0 sent
])
def test_noise_cli_verdict(tmp_path, noise, speech, ok):
    nj, sj = tmp_path / "n.json", tmp_path / "s.json"
    nj.write_text(json.dumps(noise), encoding="utf-8")
    sj.write_text(json.dumps(speech), encoding="utf-8")
    r = _cli("noise", "--noise-json", str(nj), "--speech-json", str(sj))
    assert r.returncode == (0 if ok else 1), r.stdout + r.stderr
    assert r.stdout.startswith("PASS #968" if ok else "FAIL #968")


def test_gpu_cli_fails_without_cuda(tmp_path, monkeypatch, capsys):
    import tinglongwu.device as device

    monkeypatch.setattr(device, "resolve_device", lambda *a, **k: "cpu")  # same verdict on a PC with a GPU
    log = tmp_path / "doctor.txt"
    log.write_text("[  ok] speech model\n", encoding="utf-8")
    assert hc.main(["gpu", "--doctor-log", str(log)]) == 1
    out = capsys.readouterr().out
    assert out.startswith("FAIL #966") and "'cpu'" in out
