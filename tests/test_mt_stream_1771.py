"""#1771: the final subtitle line fills in as Hy-MT2 decodes it (token streaming); only the finished text is saved,
spoken or noted. No real models: a fake llama.cpp that streams a fixed answer token by token."""
from __future__ import annotations

import json
import threading
import time

import pytest

from tinglongwu import cli
from tinglongwu.hymt import HyMtTranslator, JaRouter, hymt_path, partial_text, stream_engine, streaming
from tinglongwu.pipeline import run
from tinglongwu.segment import Segmenter
from tinglongwu.sink import JsonlSaveSink, Result, Sink
from tinglongwu.stream import PacedSource

TOKENS = ["Hello", ",", " world", ".", " How", " are", " you", "?"]
TOK_S = 0.04


class StreamLlm:
    """llama_cpp.Llama stand-in: the same answer unstreamed or as chat-completion chunks (TOK_S per token)."""

    def __init__(self, tokens=TOKENS, tok_s=0.0):
        self.tokens, self.tok_s, self.streamed = list(tokens), tok_s, 0

    def create_chat_completion(self, messages, stream=False, **kw):
        if not stream:
            time.sleep(self.tok_s * len(self.tokens))
            return {"choices": [{"message": {"content": "".join(self.tokens)}}]}
        self.streamed += 1
        return self._chunks()

    def _chunks(self):
        yield {"choices": [{"delta": {"role": "assistant"}}]}
        for t in self.tokens:
            time.sleep(self.tok_s)
            yield {"choices": [{"delta": {"content": t}}]}
        yield {"choices": [{"delta": {}, "finish_reason": "stop"}]}


def _hy(tmp_path, llm):
    p = hymt_path(tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")
    return HyMtTranslator(tmp_path, loader=lambda path, gpu: llm)


def test_streamed_partials_end_with_the_unstreamed_translation(tmp_path):
    """Acceptance 1: >= 2 partial updates for one line; the last equals the non-streamed translation."""
    plain = _hy(tmp_path / "a", StreamLlm()).translate("Hallo Welt. Wie geht's?", "de", "en").text
    llm = StreamLlm()
    hy = _hy(tmp_path / "b", llm)
    parts = []
    with streaming(hy, parts.append):
        final = hy.translate("Hallo Welt. Wie geht's?", "de", "en").text
    assert llm.streamed == 1 and len(parts) >= 2
    assert parts[-1] == final == plain == "Hello, world. How are you?"
    assert all(parts[-1].startswith(p.rstrip(".,?")) for p in parts)  # grows, never jumps around
    assert hy.on_partial is None  # streaming() is scoped to the with block


def test_drafts_and_no_callback_never_stream(tmp_path):
    llm = StreamLlm()
    hy = _hy(tmp_path, llm)
    hy.translate("Hallo", "de", "en")
    with streaming(hy, lambda t: None):
        hy.translate("Hallo", "de", "en", draft=True)
    assert llm.streamed == 0


def test_router_and_shared_lock_release(tmp_path):
    """Through JaRouter (the listen path) too; the SharedLlama lock is free again after the streamed answer."""
    p = hymt_path(tmp_path)
    p.parent.mkdir(parents=True)
    p.write_bytes(b"x")
    llm = StreamLlm()
    router = JaRouter(None, tmp_path, factory=lambda m, z, d: HyMtTranslator(m, z, d, loader=lambda path, gpu: llm),
                      all_pairs=True)
    assert stream_engine(router) is router.hy
    parts = []
    with streaming(router, parts.append):
        router.translate("Hallo", "de", "en")
    assert len(parts) >= 2
    assert router.hy.llm.lock.acquire(timeout=1)
    router.hy.llm.lock.release()


def test_echo_start_is_not_shown_as_partial():
    assert partial_text("Note that you should", "en") == ""
    assert partial_text("Hello", "en") == "Hello"
    assert partial_text("将以下文本", "zh") == ""


class _ShowSink(Sink):
    """A subtitle sink that shows partials (like the GUI bar)."""

    def __init__(self):
        self.lines, self.partials = [], []

    def write(self, r):
        self.lines.append(r.target)

    def write_partial(self, r):
        self.partials.append(r.target)


def test_save_and_voice_get_only_the_finished_line(tmp_path):
    """Acceptance 2: partials reach the subtitles only; the transcript file and the voice get the finished text."""
    from tinglongwu.voice import VoiceSink

    show = _ShowSink()
    spoken, done = [], threading.Event()
    voice = VoiceSink(show, lambda t: (spoken.append(t), done.set()))
    save = JsonlSaveSink(voice, tmp_path / "t.jsonl")
    save.write_partial(Result("de", "Hallo Welt", None, "Hello", "en"))
    save.write_partial(Result("de", "Hallo Welt", None, "Hello, wor", "en"))
    save.write(Result("de", "Hallo Welt", None, "Hello, world.", "en"))
    assert done.wait(2)
    save.f.close()
    recs = [json.loads(x) for x in (tmp_path / "t.jsonl").read_text("utf-8").splitlines()]
    assert [r["dst"] for r in recs] == ["Hello, world."]
    assert spoken == ["Hello, world."]
    assert show.partials == ["Hello", "Hello, wor"] and show.lines == ["Hello, world."]


def test_gui_partial_fills_pending_row_then_final_replaces_it():
    from tinglongwu.gui.core import PENDING_MARK, SubtitleState

    s = SubtitleState(3)
    s.apply(("source", "Hallo Welt"))
    s.apply(("partial", "Hallo Welt", "Hello, wor"))
    assert s.lines() == [("Hello, wor" + PENDING_MARK, True)]
    s.apply(("final", "Hallo Welt", "Hello, world."))
    assert s.lines() == [("Hello, world.", False)] and s.partial == {}
    assert s.history() == [("Hallo Welt", "Hello, world.", False)]


def test_gui_amend_and_drop_clear_a_stale_streamed_draft_1784():
    from tinglongwu.gui.core import SubtitleState

    s = SubtitleState(3)
    s.apply(("final", "prev", "p"))
    s.apply(("partial", "a", "x"))
    assert s.lines()[-1] == ("x", True)
    s.apply(("amend", "prev", "a", "y"))
    assert s.lines() == [("y", False)]
    s.apply(("partial", "b", "x"))
    assert s.apply(("drop", "b")) is True
    assert s.lines() == [("y", False)]


# --- pipeline: live paced clip through run() with the streaming fake (Acceptance 3: t_target_first_ms) ---

def _clip():
    import numpy as np

    from tinglongwu.capture.base import SAMPLE_RATE, AudioSource

    class Clip(AudioSource):
        def frames(self):
            rng = np.random.default_rng(0)
            a = np.zeros(int(1.4 * SAMPLE_RATE), np.float32)
            a[: SAMPLE_RATE // 2] = rng.uniform(-0.2, 0.2, SAMPLE_RATE // 2)
            for i in range(0, len(a), SAMPLE_RATE // 20):
                yield a[i:i + SAMPLE_RATE // 20]

        def close(self):
            pass
    return Clip()


def _vad(buf):
    import numpy as np

    idx = np.flatnonzero(np.abs(buf) > 0.01)
    return [(int(idx[0]), int(idx[-1]) + 1)] if len(idx) else []


class _Asr:
    def transcribe(self, seg):
        from tinglongwu.asr import Transcript

        return Transcript("Hallo Welt. Wie geht's?", "de", 0.0)


def _run(tmp_path, mt_stream):
    llm = StreamLlm(tok_s=TOK_S)
    sink, records = _ShowSink(), []
    run(PacedSource(_clip()), Segmenter(chunk=10.0, use_vad=True, step=0.25, vad_fn=_vad), _Asr(),
        _hy(tmp_path, llm), sink, "en", merger=None, audio_clock=False, on_record=records.append, mt_stream=mt_stream)
    return sink, records


@pytest.mark.parametrize("mt_stream", [True, False])
def test_pipeline_streams_and_records_time_to_first_target(tmp_path, mt_stream):
    sink, records = _run(tmp_path, mt_stream)
    assert sink.lines == ["Hello, world. How are you?"] and len(records) == 1
    rec = records[0]
    first, mt_ms = rec["t_target_first_ms"], rec["mt"] * 1000
    if mt_stream:
        assert len(sink.partials) >= 2 and sink.partials[-1] == sink.lines[0]
        # first token after ~1 of 8 tokens; the finished line after all 8
        assert first < mt_ms - 3 * TOK_S * 1000
    else:
        assert sink.partials == []
        assert first == pytest.approx(mt_ms, abs=20)


def test_flag_turns_it_off():
    assert cli.parse_args(["--no-mt-stream"]).no_mt_stream is True
    assert cli.parse_args([]).no_mt_stream is False
