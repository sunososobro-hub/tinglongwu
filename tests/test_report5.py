import io
import queue
import threading
import time
import warnings

import numpy as np
import pytest

from tinglongwu import asr, cli, pipeline, qwen
from tinglongwu.merge import SentenceMerger
from tinglongwu.qwen import (CONTEXT_MAX_CHARS, QwenTranslator, build_prompt, clean_output,
                             trim_context)
from tests.test_qwen import FakeLlm, fake_models


# 1. defaults / merge latency
def test_defaults_low_latency():
    a = cli.parse_args(["--preset", "chat"])
    assert a.chunk == 4.0 and a.max_wait == 1.5 and not a.no_merge
    assert cli.parse_args(["--chunk", "8", "--max-wait", "3", "--no-merge"]).max_wait == 3.0


def test_merge_holds_fragment_at_most_max_wait():
    m = SentenceMerger(1.5)
    assert m.add("どんぶらこを", "ja", 0.0) == []
    assert m.deadline == pytest.approx(1.5)
    assert m.due(1.0) == []
    u = m.due(1.5)
    assert [x.text for x in u] == ["どんぶらこを"]
    # a complete sentence is released immediately (no added wait)
    assert [x.text for x in m.add("やま。", "ja", 5.0)] == ["やま。"]


# 2. qwen context
def test_context_in_prompt_and_capped(tmp_path):
    m = fake_models(tmp_path, "qwen3-8b")
    llm = FakeLlm("譯文")
    t = QwenTranslator(m, "qwen3-8b", "tw", "cuda", loader=lambda p, gpu: llm)
    t.translate("one", "ja", "zh")
    assert "[Earlier]" not in llm.calls[0][0]
    t.translate("two", "ja", "zh")
    p2 = llm.calls[1][0]
    assert "one => 譯文" in p2 and "ONLY" in p2 and p2.rstrip().endswith("two<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>".rstrip()) or "[Translate]\ntwo" in p2
    for w in ("a", "b", "c"):
        t.translate(w, "ja", "zh")
    assert len(t.history) == qwen.CONTEXT_LINES
    t.translate("x", "ru", "zh")  # language change resets context
    assert "[Earlier]" not in llm.calls[-1][0]
    big = [("x" * 400, "y" * 10), ("s", "t")]
    assert trim_context(big) == [("s", "t")]
    assert sum(len(a) + len(b) for a, b in trim_context([("a" * 100, "b" * 100)] * 5)) <= CONTEXT_MAX_CHARS
    assert "[Earlier]" not in build_prompt("sys", "hi")


# 3. whisper context
class FakeSeg:
    def __init__(self, t):
        self.text, self.no_speech_prob, self.avg_logprob = t, 0.0, -0.1


class FakeModel:
    def __init__(self, texts):
        self.texts, self.prompts = list(texts), []

    def transcribe(self, audio, **kw):
        self.prompts.append(kw["initial_prompt"])
        return [FakeSeg(self.texts.pop(0))], type("I", (), {"language": "ja"})()


def make_asr(texts):
    r = asr.FasterWhisperRecognizer.__new__(asr.FasterWhisperRecognizer)
    r.model, r.lang, r._last = FakeModel(texts), "ja", ""
    return r


def test_whisper_initial_prompt_and_garbage_guard():
    long = " ".join(f"word{i}x{i*7}" for i in range(40))
    r = make_asr(["first sentence here", long, long, "next", "ok"])
    a = np.zeros(10, np.float32)
    for _ in range(5):
        r.transcribe(a)
    p = r.model.prompts
    assert p[0] is None and p[1] == "first sentence here"
    assert len(p[2]) == asr.PROMPT_MAX_CHARS
    assert p[3] is None  # previous output was a repeat -> prompt dropped
    assert p[4] == "next"
    assert asr.is_garbage("la la la la la la la la la la la la la la la la la la la la")
    assert not asr.is_garbage("hello there", "something else")


# 4. cleaning
@pytest.mark.parametrize("raw,want", [
    ("可可_chan", "可可chan"),
    ("foo_bar", "foo bar"),
    ("**你好**", "你好"),
    ('"你好"', "你好"),
    ("Translation: 你好", "你好"),
    ("翻譯：你好", "你好"),
    ("`code` 你好", "code 你好"),
    ("「你好」，他說", "「你好」，他說"),
])
def test_clean_output_artifacts(raw, want):
    assert clean_output(raw) == want


# 5. warnings
def test_discontinuity_warning_suppressed(monkeypatch):
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        monkeypatch.setattr(cli, "parse_args", lambda argv=None: (_ for _ in ()).throw(SystemExit(0)))
        with pytest.raises(SystemExit):
            cli.main([])
        warnings.warn("data discontinuity in recording", RuntimeWarning)
    assert not [x for x in w if "discontinuity" in str(x.message)]


# 6. ctrl+c
def test_pipeline_wakes_without_blocking_forever():
    """No merger deadline and a silent source: the consumer must poll (short get timeout), not block."""
    seen = []
    real_get = queue.Queue.get

    def spy(self, block=True, timeout=None):
        seen.append(timeout)
        return real_get(self, block, timeout)

    class Slow:
        def frames(self):
            time.sleep(0.6)
            return iter(())

        def close(self):
            pass

    from tinglongwu.segment import Segmenter
    orig = queue.Queue.get
    queue.Queue.get = spy
    try:
        pipeline.run(Slow(), Segmenter(1.0, use_vad=False), None, None, None)
    finally:
        queue.Queue.get = orig
    assert seen and all(t is not None and t <= pipeline.POLL for t in seen)


def test_keyboard_interrupt_hard_exits(monkeypatch):
    codes = []
    monkeypatch.setattr(cli, "_hard_exit", lambda c: codes.append(c))
    monkeypatch.setattr(cli, "models_dir", lambda d: (_ for _ in ()).throw(KeyboardInterrupt()))
    assert cli.main([]) == 0 and codes == [0]
