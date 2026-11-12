"""#90: end-to-end self-test - behaviour with fake model loaders, plus the real CLI in a child process."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from tinglongwu.selftest import CLIP_TEXT, apply_to_gui, load_clip, run_selftest, write_log
from tinglongwu.translate import Translation

ROOT = Path(__file__).resolve().parents[1]


class FakeAsr:
    def __init__(self, device, text=CLIP_TEXT):
        self.device, self.text = device, text

    def transcribe(self, audio):
        assert audio.dtype == np.float32 and audio.ndim == 1 and len(audio) > 16000
        return SimpleNamespace(text=self.text) if self.text else None


class FakeMt:
    def __init__(self, out="你好，這是翻譯系統的測試。"):
        self.out = out

    def translate(self, text, src, tgt):
        assert (text, src, tgt) == (CLIP_TEXT, "en", "zh")
        return Translation(pivot=None, text=self.out)  # the real type: #398 selftest read a missing .target


def run(tmp_path, *, cuda=True, gpu="NVIDIA GeForce RTX 3050", asr=None, mt=None, **kw):
    calls = []

    def make_asr(mdir, model, device):
        calls.append(("asr", model, device))
        return (asr or (lambda m, d: FakeAsr(d)))(model, device)

    def make_mt(mdir, eng, variant, device):
        calls.append(("mt", eng, device))
        return (mt or (lambda e, d: FakeMt()))(eng, device)

    kw.setdefault("model", "large-v3-turbo")
    res = run_selftest(tmp_path, make_asr=make_asr, make_mt=make_mt,
                       resolve=lambda choice, warn: "cuda" if cuda and choice != "cpu" else "cpu",
                       gpu_name=lambda: gpu, choose=lambda e, d, m: "hymt2" if d == "cuda" else "hymt2-cpu",
                       pick=lambda m, d: "large-v3-turbo" if d == "cuda" else "small", **kw)
    return res, calls


def test_bundled_clip_is_a_few_seconds_of_16k_mono_speech():
    a = load_clip()
    assert a.dtype == np.float32 and a.ndim == 1
    assert 3 * 16000 <= len(a) <= 10 * 16000
    assert float(np.abs(a).max()) > 0.2 and float(np.sqrt((a ** 2).mean())) > 0.02  # not silence


def test_healthy_gpu_everything_ok_no_downgrade(tmp_path):
    res, calls = run(tmp_path)
    assert res.ok and not res.downgraded
    assert (res.device, res.model, res.engine) == ("cuda", "large-v3-turbo", "hymt2")
    assert calls == [("asr", "large-v3-turbo", "cuda"), ("mt", "hymt2", "cuda")]
    text = res.text()
    assert text.count("[OK]") == 3 and "FAIL" not in text and "self-test PASSED: device=cuda" in text
    assert "heard: Hello" in res.steps[1].line() and " s)" in res.steps[1].line()


def test_old_driver_gpu_fails_with_driver_hint_then_cpu_tier_ok(tmp_path):
    # #89: nvidia-smi sees the GTX 1050 but the runtime reports no usable CUDA device
    (tmp_path / "whisper-small").mkdir()
    res, calls = run(tmp_path, cuda=False, gpu="NVIDIA GeForce GTX 1050")
    assert res.ok and res.downgraded and (res.device, res.model) == ("cpu", "small")
    assert res.steps[0].name == "GPU" and not res.steps[0].ok and "driver" in res.steps[0].advice
    assert "GTX 1050" in res.steps[0].detail
    assert calls[0] == ("asr", "small", "cpu")
    assert calls[-1] == ("mt", "hymt2-cpu", "cpu")
    assert "self-test PASSED after downgrade" in res.text()


def test_whisper_crash_on_gpu_retries_on_cpu_with_smaller_installed_model(tmp_path):
    (tmp_path / "whisper-base").mkdir()

    def asr(model, dev):
        if dev == "cuda":
            raise RuntimeError("cuDNN failed to initialize")
        return FakeAsr(dev)

    res, calls = run(tmp_path, asr=asr)
    assert res.ok and res.downgraded and (res.device, res.model) == ("cpu", "base")
    assert calls[:2] == [("asr", "large-v3-turbo", "cuda"), ("asr", "base", "cpu")]
    fail = res.steps[1]
    assert not fail.ok and "cuDNN" in fail.detail and "driver" in fail.advice
    assert calls[-1] == ("mt", "hymt2-cpu", "cpu")


def test_auto_model_is_picked_again_for_the_cpu_tier(tmp_path):
    res, calls = run(tmp_path, model=None, asr=lambda m, d: (_ for _ in ()).throw(OSError("oom")) if d == "cuda"
                     else FakeAsr(d))
    assert res.ok and calls[:2] == [("asr", "large-v3-turbo", "cuda"), ("asr", "small", "cpu")]


def test_hymt_failing_on_gpu_retries_hymt_on_cpu_never_opus(tmp_path):
    def mt(eng, dev):
        if eng == "hymt2":
            raise SystemExit("tlwu: Hy-MT2 failed to load on cuda: out of memory")
        return FakeMt()

    res, calls = run(tmp_path, mt=mt)
    assert res.ok and res.downgraded and res.engine == "hymt2"
    assert [c for c in calls if c[0] == "mt"] == [("mt", "hymt2", "cuda"), ("mt", "hymt2-cpu", "cuda")]
    assert "out of memory" in res.steps[-2].detail and res.steps[-1].name == "translation hymt2 on CPU"
    assert "opus" not in json.dumps([c for c in calls])


def test_missing_hymt_on_cpu_is_a_fail_with_exit_1_reason(tmp_path):
    def mt(eng, dev):
        raise SystemExit("tlwu: the translation model Hy-MT2 is not installed. Run `tlwu-setup --hymt` once")

    res, _ = run(tmp_path, cuda=False, gpu=None, mt=mt)
    assert not res.ok and "tlwu-setup --hymt" in res.steps[-1].detail
    assert "self-test FAILED" in res.text()


def test_translation_without_chinese_is_a_fail(tmp_path):
    res, _ = run(tmp_path, cuda=False, gpu=None, mt=lambda e, d: FakeMt("Hello hello"))
    assert not res.ok and "no Chinese" in res.steps[-1].detail


def test_empty_transcript_on_cpu_is_a_fail(tmp_path):
    res, calls = run(tmp_path, cuda=False, gpu=None, asr=lambda m, d: FakeAsr(d, text=""))
    assert not res.ok and "no text" in res.steps[-1].detail and not [c for c in calls if c[0] == "mt"]


def test_log_appends_and_gui_settings_get_tested_cpu_config(tmp_path):
    (tmp_path / "whisper-small").mkdir()
    res, _ = run(tmp_path, cuda=False, gpu="NVIDIA GeForce GTX 1050")
    log = tmp_path / "logs" / "install.log"
    assert write_log(res, log) == log and write_log(res, log) == log
    body = log.read_text(encoding="utf-8")
    assert body.count("--- tlwu self-test") == 2 and "[FAIL] GPU" in body
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"model": "large-v3-turbo", "engine": "auto", "live_draft": True}), "utf-8")
    assert apply_to_gui(res, "large-v3-turbo", settings)
    saved = json.loads(settings.read_text("utf-8"))
    assert saved["model"] == "small" and saved["live_draft"] is False and saved["engine"] == "auto"


def test_cli_selftest_child_process_reports_and_exits_1_without_models(tmp_path):
    # real `python -m tinglongwu --selftest` with an empty models dir: the speech step fails, exit 1, install.log written
    env = dict(os.environ, TLWU_MODELS=str(tmp_path / "models"), TLWU_LOG_DIR=str(tmp_path / "logs"),
               HOME=str(tmp_path), APPDATA=str(tmp_path), XDG_CONFIG_HOME=str(tmp_path), XDG_STATE_HOME=str(tmp_path),
               PATH=str(tmp_path))  # no nvidia-smi on PATH -> CPU
    p = subprocess.run([sys.executable, "-m", "tinglongwu", "--selftest", "--model", "tiny", "--device", "cpu"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=ROOT, env=env,
                       timeout=300)
    assert p.returncode == 1, p.stdout + p.stderr
    assert "[OK] device" in p.stdout and "[FAIL] speech model tiny on CPU" in p.stdout
    assert "self-test FAILED" in p.stdout and "unrecognized arguments" not in p.stderr
    logs = list(tmp_path.rglob("install.log"))
    assert logs and "self-test FAILED" in logs[0].read_text(encoding="utf-8")
