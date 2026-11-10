"""#90: end-to-end self-test - behaviour with fake model loaders, plus the real CLI in a child process."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from tinglongwu import selftest
from tinglongwu.selftest import CLIP_TEXT, Result, apply_to_gui, load_clip, run_selftest, write_log

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
        return SimpleNamespace(target=self.out)


def run(tmp_path, *, cuda=True, gpu="NVIDIA GeForce RTX 3050", asr=None, mt=None, engine="qwen3-8b", **kw):
    calls = []

    def make_asr(mdir, model, device):
        calls.append(("asr", model, device))
        return (asr or (lambda m, d: FakeAsr(d)))(model, device)

    def make_mt(mdir, eng, variant, device):
        calls.append(("mt", eng, device))
        return (mt or (lambda e, d: FakeMt()))(eng, device)

    def resolve(choice, warn):
        if choice == "cpu" or not cuda:
            return "cpu"
        return "cuda"

    kw.setdefault("model", "large-v3-turbo")
    res = run_selftest(tmp_path, make_asr=make_asr, make_mt=make_mt, resolve=resolve, gpu_name=lambda: gpu,
                       choose=lambda e, d, m: engine if d == "cuda" else "opus",
                       has_engine=lambda m, n: True, **kw)
    return res, calls


def test_bundled_clip_is_a_few_seconds_of_16k_mono_speech():
    a = load_clip()
    assert a.dtype == np.float32 and a.ndim == 1
    assert 3 * 16000 <= len(a) <= 10 * 16000
    assert float(np.abs(a).max()) > 0.2 and float(np.sqrt((a ** 2).mean())) > 0.02  # not silence


def test_healthy_gpu_everything_ok_no_downgrade(tmp_path):
    res, calls = run(tmp_path)
    assert res.ok and not res.downgraded
    assert (res.device, res.model, res.engine) == ("cuda", "large-v3-turbo", "qwen3-8b")
    assert all(s.ok for s in res.steps) and len(res.steps) == 3
    assert calls == [("asr", "large-v3-turbo", "cuda"), ("mt", "qwen3-8b", "cuda")]
    text = res.text()
    assert text.count("[OK]") == 3 and "FAIL" not in text and "self-test PASSED: device=cuda" in text
    assert " s)" in res.steps[1].line() and "heard: Hello" in res.steps[1].line()


def test_old_driver_gpu_fails_with_driver_hint_then_cpu_tier_ok(tmp_path):
    # #89: nvidia-smi sees the GTX 1050 but the runtime reports no usable CUDA device
    (tmp_path / "whisper-small").mkdir()
    res, calls = run(tmp_path, cuda=False, gpu="NVIDIA GeForce GTX 1050")
    assert res.ok and res.downgraded
    assert (res.device, res.model, res.engine) == ("cpu", "small", "opus")
    first = res.steps[0]
    assert first.name == "GPU" and not first.ok and "GTX 1050" in first.detail
    assert "update the NVIDIA graphics driver" in first.advice
    assert calls == [("asr", "small", "cpu"), ("mt", "opus", "cpu")]
    assert "[FAIL] GPU" in res.text() and "self-test PASSED after downgrade: device=cpu" in res.text()


def test_gpu_inference_crash_downgrades_to_cpu_and_retests(tmp_path):
    class Crash(FakeAsr):
        def transcribe(self, audio):
            if self.device == "cuda":
                raise RuntimeError("Library cudnn_ops64_9.dll is not found or cannot be loaded")
            return super().transcribe(audio)

    res, calls = run(tmp_path, asr=lambda m, d: Crash(d))
    assert res.ok and res.downgraded and res.device == "cpu" and res.engine == "opus"
    assert [c for c in calls if c[0] == "asr"] == [("asr", "large-v3-turbo", "cuda"), ("asr", "large-v3-turbo", "cpu")]
    failed = [s for s in res.steps if not s.ok]
    assert len(failed) == 1 and "cudnn" in failed[0].detail and "driver" in failed[0].advice


def test_recognizer_silent_cpu_fallback_is_reported_as_gpu_fail(tmp_path):
    res, _ = run(tmp_path, asr=lambda m, d: FakeAsr("cpu"))  # loader fell back by itself
    assert res.ok and res.downgraded and res.device == "cpu"
    assert any(not s.ok and "on GPU" in s.name for s in res.steps)


def test_qwen_failure_steps_down_to_4b_then_opus(tmp_path):
    def mt(eng, dev):
        if eng.startswith("qwen"):
            raise OSError("exception: access violation reading 0x0 (illegal instruction)")
        return FakeMt()

    res, calls = run(tmp_path, mt=mt)
    assert res.ok and res.engine == "opus" and res.engine_downgraded and res.device == "cuda"
    assert [c[1] for c in calls if c[0] == "mt"] == ["qwen3-8b", "qwen3-4b", "opus"]
    assert sum(not s.ok for s in res.steps) == 2 and "using opus instead" in res.text()


def test_everything_failing_is_reported_and_not_ok(tmp_path):
    def mt(eng, dev):
        raise RuntimeError("boom")

    res, _ = run(tmp_path, cuda=False, gpu=None, mt=mt, model="small")
    assert not res.ok and "self-test FAILED" in res.text() and "boom" in res.text()
    res, _ = run(tmp_path, cuda=False, gpu=None, asr=lambda m, d: FakeAsr(d, text=""), model="small")
    assert not res.ok and "no text" in res.text()
    res, _ = run(tmp_path, cuda=False, gpu=None, mt=lambda e, d: FakeMt("hello only latin"), model="small")
    assert not res.ok and "no Chinese" in res.text()


def test_apply_to_gui_writes_downgraded_config(tmp_path):
    from tinglongwu.gui import core

    p = tmp_path / "gui.json"
    core.save_settings({**core.DEFAULTS, "model": "large-v3-turbo", "engine": "auto", "font_size": 40}, p)
    res = Result(device="cpu", model="small", engine="opus", downgraded=True, engine_downgraded=True, ok=True)
    assert apply_to_gui(res, "large-v3-turbo", "auto", p)
    s = json.loads(p.read_text("utf-8"))
    assert (s["model"], s["engine"], s["live_draft"], s["font_size"]) == ("small", "opus", False, 40)


def test_apply_to_gui_keeps_user_choices_when_nothing_was_downgraded(tmp_path):
    from tinglongwu.gui import core

    p = tmp_path / "gui.json"
    core.save_settings({**core.DEFAULTS, "model": "large-v3-turbo", "engine": "qwen"}, p)
    apply_to_gui(Result(device="cuda", model="small", engine="qwen3-8b", ok=True), "small", "auto", p)
    s = json.loads(p.read_text("utf-8"))
    assert (s["model"], s["engine"], s["live_draft"]) == ("large-v3-turbo", "qwen", True)
    fresh = tmp_path / "new" / "gui.json"  # fresh install: the tested model becomes the default
    apply_to_gui(Result(device="cuda", model="large-v3-turbo", engine="qwen3-8b", ok=True),
                 "large-v3-turbo", "auto", fresh)
    s = json.loads(fresh.read_text("utf-8"))
    assert (s["model"], s["engine"]) == ("large-v3-turbo", "auto")


def test_write_log_appends_to_install_log(tmp_path):
    res, _ = run(tmp_path, cuda=False, gpu="NVIDIA GeForce GTX 1050", model="small")
    p = write_log(res)
    write_log(res)
    assert p == Path(os.environ["TLWU_LOG_DIR"]) / "install.log"
    text = p.read_text("utf-8")
    assert text.count("tlwu self-test") == 2 and "[FAIL] GPU" in text and "self-test PASSED" in text


@pytest.mark.skipif(sys.platform == "win32", reason="shell stub for nvidia-smi is POSIX-only")
def test_real_cli_reports_fail_with_setup_hint_and_writes_log(tmp_path):
    # real process, real loaders: no models installed, nvidia-smi (stub) sees a card the runtime cannot use
    stub = tmp_path / "bin" / "nvidia-smi"
    stub.parent.mkdir()
    stub.write_text("#!/bin/sh\necho 'NVIDIA GeForce GTX 1050, 2048'\n")
    stub.chmod(0o755)
    env = dict(os.environ, PATH=f"{stub.parent}{os.pathsep}{os.environ.get('PATH', '')}",
               TLWU_LOG_DIR=str(tmp_path / "log"), APPDATA=str(tmp_path / "appdata"),
               HOME=str(tmp_path / "home"), CUDA_VISIBLE_DEVICES="-1",
               PYTHONPATH=str(ROOT) + os.pathsep + os.environ.get("PYTHONPATH", ""))
    r = subprocess.run([sys.executable, "-m", "tinglongwu", "--selftest", "--models-dir", str(tmp_path / "models")],
                       capture_output=True, text=True, env=env, timeout=120)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "Traceback" not in r.stderr
    assert "[FAIL] GPU" in r.stdout and "GTX 1050" in r.stdout and "update the NVIDIA graphics driver" in r.stdout
    assert "[FAIL] speech model small on CPU" in r.stdout and "tlwu-setup --whisper small" in r.stdout
    assert "self-test FAILED" in r.stdout
    log = (tmp_path / "log" / "install.log").read_text("utf-8")
    assert "self-test FAILED" in log and "[FAIL] GPU" in log
    assert not (tmp_path / "appdata" / "tinglongwu" / "gui.json").exists()  # a failed run changes no settings
