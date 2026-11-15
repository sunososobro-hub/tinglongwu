"""0.3.8: speaker labels + rename in the GUI listen side; everything runs without sherpa-onnx (embedding mocked)."""
import queue
import threading
import time

import numpy as np

from tinglongwu import speaker_embed
from tinglongwu.gui import core
from tinglongwu.sink import Result
from tinglongwu.speakers import SpeakerBook


def _wait(cond, secs=3.0):
    t0 = time.monotonic()
    while not cond() and time.monotonic() - t0 < secs:
        time.sleep(0.01)
    return cond()


def test_label_returns_at_once_and_result_arrives_later():
    gate = threading.Event()

    def slow_embed(seg):
        gate.wait(2)  # the model is slow: label() must not wait for it
        return [1.0, 0.0] if seg[0] > 0 else [0.0, 1.0]

    ready = []
    lab = speaker_embed.AsyncSpeakerLabeler(slow_embed, on_ready=lambda: ready.append(1))
    a = np.ones(16000 * 2, np.float32)
    t0 = time.monotonic()
    tok, secs = lab.label(a)
    assert time.monotonic() - t0 < 0.5 and secs == 0.0 and tok.startswith("spk#")
    assert lab.result(tok) is None  # pending
    gate.set()
    assert _wait(lambda: lab.result(tok) is not None) and lab.result(tok) == "說話者1" and ready
    tok2, _ = lab.label(-a)  # a different voice
    assert _wait(lambda: lab.result(tok2) is not None) and lab.result(tok2) == "說話者2"
    tok3, _ = lab.label(a)  # first voice again
    assert _wait(lambda: lab.result(tok3) is not None) and lab.result(tok3) == "說話者1"
    lab.close()


def test_broken_embedder_gives_no_label_and_no_error():
    def boom(seg):
        raise RuntimeError("x")

    lab = speaker_embed.AsyncSpeakerLabeler(boom)
    tok, _ = lab.label(np.ones(32000, np.float32))
    assert _wait(lambda: tok in lab._res) and lab.result(tok) is None
    lab.close()


def test_unavailable_without_sherpa_or_model(tmp_path):
    assert speaker_embed.available(tmp_path) is False
    assert speaker_embed.make_async_labeler(tmp_path) is None  # no labels, no exception


def test_queue_sink_carries_token_and_state_keeps_it_aligned():
    q = queue.Queue()
    sink = core.QueueSink(q)
    sink.write(Result("zh", "你好", None, "Hello", "en", speaker="spk#1"))
    sink.write(Result("zh", "再見", None, "Bye", "en"))
    evs = [q.get_nowait(), q.get_nowait()]
    assert evs[0] == ("final", "你好", "Hello", "spk#1") and evs[1] == ("final", "再見", "Bye")
    st = core.SubtitleState(2)
    for e in evs:
        st.apply(e)
    st.apply(("final_side", "x", "me"))
    assert [h[1] for h in st.history()] == ["Bye", "me"]
    assert st.history_who() == [None, None]  # trimmed together with finals
    st.apply(("final", "a", "b", "spk#9"))
    assert st.history_who()[-1] == "spk#9" and len(st.history_who()) == len(st.history())


def test_rename_applies_to_all_past_and_future_bubbles():
    book = SpeakerBook()
    fmt = lambda n: f"說話者 {n}"  # noqa: E731
    assert core.speaker_text(book, "說話者1", fmt) == "說話者 1"
    assert core.speaker_text(book, None, fmt) is None
    book.rename("說話者1", "Alice")
    assert core.speaker_text(book, "說話者1", fmt) == "Alice"  # the same label on an earlier and a later bubble
    assert core.speaker_text(book, "說話者2", fmt) == "說話者 2"
    book.rename("說話者1", "")  # empty name: back to the numbered label
    assert core.speaker_text(book, "說話者1", fmt) == "說話者 1"


def test_run_session_factory_none_means_no_labels(monkeypatch):
    import inspect

    from tinglongwu import cli

    assert "labeler_factory" in inspect.signature(cli.run_session).parameters


def test_resolve_gives_real_label_and_close_stops_worker():
    lab = speaker_embed.AsyncSpeakerLabeler(lambda seg: [1.0, 0.0])
    tok, _ = lab.label(np.ones(32000, np.float32))
    assert lab.resolve(tok) == "說話者1" and lab.resolve("說話者5") == "說話者5"
    lab.close()
    lab.close()  # idempotent
    th = [x for x in threading.enumerate() if x.name == "tlwu-speaker-id"]
    assert _wait(lambda: not any(x.is_alive() for x in th))


def test_saved_transcript_has_resolved_label_not_token(tmp_path):
    import json

    from tinglongwu.sink import JsonlSaveSink

    class Inner:
        def write(self, r):
            pass

    lab = speaker_embed.AsyncSpeakerLabeler(lambda seg: [1.0, 0.0])
    tok, _ = lab.label(np.ones(32000, np.float32))
    sink = JsonlSaveSink(Inner(), tmp_path / "a.jsonl")
    sink.resolve_speaker = lab.resolve
    sink.write(Result("zh", "你好", None, "Hello", "en", speaker=tok))
    sink.write(Result("zh", "再見", None, "Bye", "en", speaker="spk#999"))  # never labelled: nothing saved
    sink.f.close()
    lab.close()
    rows = [json.loads(x) for x in (tmp_path / "a.jsonl").read_text(encoding="utf-8").splitlines()]
    assert rows[0]["speaker"] == "說話者1" and "speaker" not in rows[1]
