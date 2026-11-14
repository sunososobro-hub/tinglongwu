"""#782: ASR engine selection cuda > vulkan > cpu with a mocked Vulkan backend (no binaries)."""
from __future__ import annotations

import numpy as np
import pytest

from tinglongwu import asr_vulkan as av


class FakeBackend:
    def transcribe(self, audio, language):
        return ("  hello ", "en", 0.1, -0.2)


def _pick(cuda, vulkan, env=None, vfactory=None):
    msgs = []
    rec, used = av.select_recognizer(
        "m", cuda=cuda, vulkan=vulkan, env=env or {}, warn=msgs.append,
        cpu_factory=lambda device: ("fw", device),
        vulkan_factory=vfactory or (lambda: ("vk",)))
    return rec, used, msgs


def test_cuda_present_uses_faster_whisper_gpu():
    assert _pick(True, True) == (("fw", "cuda"), "cuda", [])


def test_no_cuda_with_vulkan_pack_uses_vulkan():
    assert _pick(False, True) == (("vk",), "vulkan", [])


def test_forced_vulkan_with_cuda_present():
    assert _pick(True, True, {"TLWU_DEVICE": "vulkan"})[1] == "vulkan"


def test_nothing_available_is_cpu():
    assert _pick(False, False) == (("fw", "cpu"), "cpu", [])


def test_vulkan_init_failure_falls_back_to_cpu_with_one_log_line():
    def boom():
        raise RuntimeError("no ICD")
    rec, used, msgs = _pick(False, True, vfactory=boom)
    assert (rec, used) == (("fw", "cpu"), "cpu")
    assert len(msgs) == 1 and "Vulkan" in msgs[0]


def test_missing_binding_falls_back_without_crash(tmp_path):
    msgs = []
    rec, used = av.select_recognizer(tmp_path, cuda=False, vulkan=True, env={}, warn=msgs.append,
                                     cpu_factory=lambda d: "cpu-engine")
    assert (rec, used) == ("cpu-engine", "cpu") and len(msgs) == 1


def test_vulkan_recognizer_uses_lazy_mock_backend(tmp_path):
    calls = []
    def loader(pack, models, size):
        calls.append(size)
        return FakeBackend()
    r = av.WhisperCppVulkanRecognizer(tmp_path, "small", "auto", pack_dir=tmp_path, backend_loader=loader)
    t = r.transcribe(np.zeros(16000, dtype=np.float32))
    assert (t.text, t.lang, calls) == ("hello", "en", ["small"])
