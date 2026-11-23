"""#1759: the live session runs ASR through asr_vulkan.select_recognizer (cuda > vulkan > cpu), with fake factories
and a fake whisper.cpp backend (no binaries, no models)."""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from tinglongwu import asr_vulkan as av
from tinglongwu import cli


class FakeBackend:
    def __init__(self, replies=None):
        self.calls = []
        self.replies = list(replies or [])

    def transcribe(self, audio, language):
        self.calls.append((len(audio), language))
        return self.replies.pop(0) if self.replies else ("hello", language or "en", 0.1, -0.2)


def _vk(backend=None, **kw):
    b = backend or FakeBackend()
    return av.WhisperCppVulkanRecognizer("m", "small", kw.pop("lang", "auto"), pack_dir="p",
                                         backend_loader=lambda *_: b, **kw), b


def _args(**kw):
    base = dict(model="small", lang="en", compute="auto", lang_file=None, cpu_beam=2, cpu_threads=4)
    base.update(kw)
    return SimpleNamespace(**base)


def _live(dev="cpu", vulkan=True, ready=True, env=None, vk_raises=False, **akw):
    """cli.live_recognizer with fake engine factories in select_recognizer."""
    msgs, built = [], {}

    def select(*a, cpu_factory, vulkan_factory, **kw):
        def cpu(device):
            built["cpu"] = device
            return SimpleNamespace(device=device, kind="fw")

        def vk():
            if vk_raises:
                raise RuntimeError("no ICD")
            return SimpleNamespace(device="vulkan", kind="vk")
        return av.select_recognizer(*a, cpu_factory=cpu, vulkan_factory=vk, **kw)

    rec, used = cli.live_recognizer(_args(**akw), "m", dev, select=select, vulkan=lambda: vulkan,
                                    ready=lambda mdir, size: ready, env=env or {}, say=msgs.append)
    return rec, used, msgs


def test_no_cuda_with_vulkan_ready_uses_vulkan_and_says_so():
    rec, used, msgs = _live()
    assert (rec.kind, used) == ("vk", "vulkan")
    assert msgs == ["tlwu: speech model small on vulkan (whisper.cpp)"]


def test_vulkan_init_failure_is_cpu_with_exactly_one_warning():
    rec, used, msgs = _live(vk_raises=True)
    assert (rec.kind, rec.device, used) == ("fw", "cpu", "cpu")
    warns = [m for m in msgs if "Vulkan ASR init failed" in m]
    assert len(warns) == 1 and msgs[-1] == "tlwu: speech model small on CPU"


def test_cuda_present_is_unchanged():
    rec, used, msgs = _live(dev="cuda")
    assert (rec.kind, rec.device, used) == ("fw", "cuda", "cuda")
    assert msgs == ["tlwu: speech model small on GPU"]


def test_auto_without_whisper_cpp_model_stays_on_cpu_silently():
    rec, used, msgs = _live(ready=False)
    assert (rec.kind, used) == ("fw", "cpu") and msgs == ["tlwu: speech model small on CPU"]


def test_forced_vulkan_tries_even_without_model_check():
    rec, used, _ = _live(ready=False, env={"TLWU_DEVICE": "vulkan"})
    assert used == "vulkan"


def test_device_cpu_never_uses_vulkan():
    rec, used, _ = _live(compute="cpu", env={"TLWU_DEVICE": "vulkan"})
    assert (rec.kind, used) == ("fw", "cpu")


def test_vulkan_ready_needs_pack_build_and_model(tmp_path):
    pack, mdir = tmp_path / "pack", tmp_path / "models"
    assert not av.vulkan_ready(mdir, "small", pack_dir=pack)
    (pack / av.WHISPER_DIR).mkdir(parents=True)
    (pack / av.WHISPER_DIR / av.WHISPER_LIB).write_bytes(b"x")
    assert not av.vulkan_ready(mdir, "small", pack_dir=pack)
    (mdir / av.MODEL_DIR).mkdir(parents=True)
    (mdir / av.MODEL_DIR / "ggml-small.bin").write_bytes(b"x")
    (mdir / av.MODEL_DIR / "ggml-tiny.bin").write_bytes(b"x")
    assert av.vulkan_ready(mdir, "small", pack_dir=pack)
    assert av.installed_sizes(mdir) == ["tiny", "small"]


# (2) every recognizer attribute the live path uses


def test_live_attribute_is_accepted_noop():
    r, b = _vk()
    r.live = True
    assert r.transcribe(np.zeros(16000, np.float32)).text == "hello"


def test_warm_up_decodes_once_and_keeps_state():
    r, b = _vk(lang="auto")
    r.warm_up()
    assert b.calls == [(16000, "en")] and r.lang is None


def test_glossary_normalizes_numbers_hotwords_noop():
    class G:
        def normalize_numbers(self, t):
            return t.replace("two", "2")

        def hotwords(self):
            raise AssertionError("hotwords are a no-op on whisper.cpp")
    r, _ = _vk(FakeBackend([("two cats", "en", 0.0, 0.0)]), glossary=G())
    assert r.transcribe(np.zeros(8000, np.float32)).text == "2 cats"


def test_candidates_outside_set_are_decoded_again_in_a_candidate(tmp_path):
    lf = tmp_path / "lang"
    lf.write_text("stale")
    b = FakeBackend([("bonjour", "fr", 0.0, 0.0), ("privet", "ru", 0.1, -0.3), ("hi", "en", 0.0, 0.0)])
    r, _ = _vk(b, candidates=("en", "ru"), lang_file=str(lf))
    assert not lf.exists()  # cleared at start
    t = r.transcribe(np.zeros(8000, np.float32))
    assert (t.text, t.lang, b.calls[1][1]) == ("privet", "en", "en")
    t2 = r.transcribe(np.zeros(8000, np.float32))
    assert t2.lang == "en" and lf.read_text() == "en"


def test_candidates_inside_set_write_lang_file(tmp_path):
    lf = tmp_path / "lang"
    r, b = _vk(FakeBackend([("privet", "ru", 0.0, 0.0)]), candidates=("en", "ru"), lang_file=str(lf))
    assert r.transcribe(np.zeros(8000, np.float32)).lang == "ru"
    assert lf.read_text() == "ru" and len(b.calls) == 1


def test_lang_file_ignored_without_candidates(tmp_path):
    r, _ = _vk(lang_file=str(tmp_path / "lang"))
    assert r.lang_file is None


def test_beam_and_threads_accepted_noop():
    r, b = _vk(beam=3, threads=2)
    assert (r.beam, r.threads) == (3, 2) and r.transcribe(np.zeros(8000, np.float32)).text == "hello"


def test_auto_locks_onto_first_language():
    r, b = _vk(FakeBackend([("hola", "es", 0.0, 0.0), ("hello", "es", 0.0, 0.0)]))
    r.transcribe(np.zeros(8000, np.float32))
    r.transcribe(np.zeros(8000, np.float32))
    assert r.lang == "es" and [c[1] for c in b.calls] == [None, "es"]


def test_device_attr_and_ladder_uses_whisper_cpp_sizes(tmp_path):
    r, _ = _vk()
    assert r.device == "vulkan"
    (tmp_path / av.MODEL_DIR).mkdir()
    (tmp_path / av.MODEL_DIR / "ggml-base.bin").write_bytes(b"x")
    kw = cli.vulkan_ladder(_args(), tmp_path, None, None, "vulkan")
    assert kw["installed"] == ["base"] and callable(kw["make_asr"])
    assert cli.vulkan_ladder(_args(), tmp_path, None, None, "cpu") == {}


def test_partial_draft_commits_no_language_state(tmp_path):
    lf = tmp_path / "lang"
    r, b = _vk(FakeBackend([("hola", "es", 0.0, 0.0)]))
    assert r.partial(np.zeros(8000, np.float32)).text == "hola" and r.lang is None
    rc, bc = _vk(FakeBackend([("privet", "ru", 0.0, 0.0)]), candidates=("en", "ru"), lang_file=str(lf))
    assert rc.partial(np.zeros(8000, np.float32)).lang == "ru"
    assert rc._cur is None and not lf.exists()
