import io

import numpy as np

from tinglongwu.asr import Recognizer, Transcript
from tinglongwu.capture.base import SAMPLE_RATE, AudioSource
from tinglongwu.pipeline import run
from tinglongwu.segment import Segmenter
from tinglongwu.sink import TextSink
from tinglongwu.stream import (DIM, DraftEngine, DraftRenderer, LocalAgreement, PacedSource, fit_tail)
from tinglongwu.translate import Translation, Translator


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


class FakeASR(Recognizer):
    def __init__(self, hyps, lang="en"):
        self.hyps, self.lang, self.calls = list(hyps), lang, 0

    def partial(self, audio):
        h = self.hyps[min(self.calls, len(self.hyps) - 1)]
        self.calls += 1
        return Transcript(h, self.lang)

    def transcribe(self, audio):
        return Transcript("final text.", self.lang)


class FakeMT(Translator):
    def __init__(self):
        self.drafts, self.finals = [], []

    def translate(self, text, src, tgt):
        self.finals.append(text)
        return Translation(None, "FINAL:" + text)

    def draft(self, text, src, tgt):
        self.drafts.append(text)
        return Translation(None, "D:" + text)


def tone(n=SAMPLE_RATE):
    return (0.3 * np.ones(n)).astype(np.float32)


def make(hyps, lang="en", live=None, interval=1.0):
    clock = Clock()
    state = {"live": live if live is not None else (0, tone())}
    out = io.StringIO()
    mt = FakeMT()
    eng = DraftEngine(FakeASR(hyps, lang), mt, DraftRenderer(out, columns=lambda: 80), lambda: state["live"],
                      "zh", interval=interval, clock=clock, mask_k=0)  # mask-k is tested in test_retranslation
    return eng, clock, state, out, mt


# ---- agreement ----
def test_agreement_words():
    a = LocalAgreement()
    assert a.update("hello wor") == ("", "hello wor")  # first hypothesis: nothing confirmed
    assert a.update("hello world how") == ("hello", "world how")
    assert a.update("hello world how are") == ("hello world how", "are")
    assert a.update("hello world how are you") == ("hello world how are", "you")


def test_agreement_cjk_by_characters():
    a = LocalAgreement()
    a.update("你好世", cjk=True)
    assert a.update("你好世界今天", cjk=True) == ("你好世", "界今天")
    assert a.update("你好世界今天好", cjk=True) == ("你好世界今天", "好")


def test_agreement_revision_shrinks():
    a = LocalAgreement()
    a.update("the cat sat")
    assert a.update("the cat sat down")[0] == "the cat sat"
    assert a.update("a dog ran")[0] == ""  # contradicts old prefix


def test_fit_tail_and_width():
    assert fit_tail("abcdef", 10) == ("abcdef", 0)
    text, cut = fit_tail("你好世界今天", 7)
    assert text.startswith("...") and cut > 0


# ---- engine: render, throttle ----
def test_tick_respects_interval_and_new_audio():
    eng, clock, state, out, mt = make(["hello world", "hello world again"])
    assert eng.tick() and eng.asr.calls == 1
    assert not eng.tick()  # interval not elapsed
    clock.t = 1.0
    assert not eng.tick()  # same audio: nothing new
    state["live"] = (0, tone(SAMPLE_RATE * 2))
    assert eng.tick() and eng.asr.calls == 2


def step(eng, clock, state, n, dt=1.0):
    clock.t += dt
    state["live"] = (0, tone(SAMPLE_RATE * n))
    eng.tick()
    eng.translate_pending()


def test_whole_partial_translated_at_once_zh_only_by_default():
    eng, clock, state, out, mt = make(["privet mir", "privet mir kak"], lang="ru")
    eng.tick()
    assert eng.translate_pending()  # first partial translated right away, nothing "confirmed" yet
    assert mt.drafts == ["privet mir"]
    s = out.getvalue()
    assert "[ZH]" in s and "D:privet mir" in s and "[RU]" not in s  # Chinese only


def test_stream_src_shows_source_too():
    eng, clock, state, out, mt = make(["privet mir"], lang="ru")
    eng.show_src = True
    eng.tick()
    eng.translate_pending()
    assert "[RU] " in out.getvalue() and "[ZH]" in out.getvalue()


def test_no_translation_yet_draws_nothing_but_same_lang_shows_source():
    eng, clock, state, out, mt = make(["privet mir"], lang="ru")
    eng.tick()  # translation still queued
    assert out.getvalue() == ""
    eng2, _, _, out2, _ = make(["nihao"], lang="zh")  # target language == source: no MT, show the source
    eng2.tick()
    assert "[ZH] " in out2.getvalue() and "nihao" in out2.getvalue()


def test_retranslate_on_growth_or_gap_only():
    eng, clock, state, out, mt = make(["aaaa bbbb", "aaaa bbbb c", "aaaa bbbb cc", "aaaa bbbb cccccccccc"], lang="ru", interval=0.5)
    eng.tick()
    eng.translate_pending()
    step(eng, clock, state, 2, dt=0.7)  # +2 chars, 0.7 s: too early
    assert len(mt.drafts) == 1
    step(eng, clock, state, 3, dt=0.7)  # 1.4 s since last draft: due by gap
    assert len(mt.drafts) == 2 and mt.drafts[-1] == "aaaa bbbb cc"
    step(eng, clock, state, 4, dt=0.7)  # +10 chars: due by growth
    assert len(mt.drafts) == 3 and mt.drafts[-1].endswith("cccccccccc")


def test_unchanged_hypothesis_not_retranslated():
    eng, clock, state, out, mt = make(["same words"], lang="ru")
    eng.tick()
    eng.translate_pending()
    for n in (2, 3, 4):
        step(eng, clock, state, n, dt=2.0)
    assert mt.drafts == ["same words"]


def test_latency_logging_first_draft_and_final():
    logs = []
    eng, clock, state, out, mt = make(["privet mir"], lang="ru")
    eng.log = logs.append
    state["live"] = (0, tone(SAMPLE_RATE))  # 1 s of audio already: segment started at t=-1
    clock.t = 0.0
    eng.tick()
    clock.t = 0.8
    eng.translate_pending()
    assert eng.first_zh == [1.8] and "first zh draft" in logs[0]
    clock.t = 3.0
    eng.final_written(0)
    assert eng.finals == [4.0] and "final epoch=0 4.00s" in logs[1]


def test_drafts_yield_while_final_waits_and_dont_starve():
    eng, clock, state, out, mt = make(["x y"], lang="ru")
    eng.final_waiting = 1  # a final is queued on the lock
    assert eng.tick() is False and eng.asr.calls == 0
    eng.final_waiting = 0
    with eng.final_section():
        assert eng.final_waiting == 1
    assert eng.final_waiting == 0 and eng.tick()


def test_translate_pending_keeps_request_when_busy():
    eng, clock, state, out, mt = make(["x y"], lang="ru")
    eng.tick()
    with eng.lock:
        assert eng.translate_pending() is False
    assert eng.translate_pending() and mt.drafts == ["x y"]


def test_drop_stale_translation():
    eng, clock, state, out, mt = make(["a b c", "a b c d", "a b c d e", "a b c d e f"], lang="ru")
    for n in (1, 2, 3, 4):  # four ticks, translator never runs in between
        state["live"] = (0, tone(SAMPLE_RATE * n))
        eng.tick()
        clock.t += 1
    assert eng.dropped >= 2
    eng.translate_pending()
    assert len(mt.drafts) == 1 and mt.drafts[0].split()[:3] == ["a", "b", "c"]  # only the newest


def test_stale_translation_discarded_after_segment_ends():
    eng, clock, state, out, mt = make(["a b", "a b c"], lang="ru")
    eng.tick()
    clock.t = 1.0
    state["live"] = (0, tone(SAMPLE_RATE * 2))
    eng.tick()
    state["live"] = None  # segment ended (final path takes over)
    before = out.getvalue()
    eng.translate_pending()
    assert not mt.drafts and out.getvalue() == before


def test_drafts_skip_when_model_busy():
    eng, clock, state, out, mt = make(["x y"])
    with eng.lock:  # final ASR/MT holds the models
        assert eng.tick() is False
    assert eng.asr.calls == 0


def test_new_segment_resets_agreement():
    eng, clock, state, out, mt = make(["one two", "one two three", "four five"])
    eng.tick()
    clock.t = 1
    state["live"] = (0, tone(SAMPLE_RATE * 2))
    eng.tick()
    assert eng.agree.confirmed
    clock.t = 2
    state["live"] = (1, tone())
    eng.tick()
    assert eng.agree.confirmed == [] and eng.translation == ""


# ---- renderer: draft -> final ----
def test_renderer_overwrites_in_place():
    out = io.StringIO()
    r = DraftRenderer(out, columns=lambda: 80)
    r.draw("RU", "привет", "мир", "ZH", "你好")
    assert r.rows == 2
    r.draw("RU", "привет мир", "", "ZH", "你好世界")
    s = out.getvalue()
    assert "\x1b[1A\r\x1b[J" in s  # went up one row and cleared before the redraw
    r.clear()
    assert r.rows == 0 and out.getvalue().endswith("\x1b[1A\r\x1b[J")


def test_disabled_renderer_writes_nothing():
    out = io.StringIO()
    r = DraftRenderer(out, enabled=False)
    r.draw("EN", "a", "b")
    r.clear()
    assert out.getvalue() == ""


class Frames(AudioSource):
    def __init__(self, n):
        self.n = n

    def frames(self):
        for _ in range(self.n):
            yield tone(SAMPLE_RATE // 2)


def test_draft_replaced_by_final_in_pipeline():
    out = io.StringIO()
    seg = Segmenter(chunk=1.0, use_vad=False, overlap=0.1)
    mt = FakeMT()
    renderer = DraftRenderer(out, columns=lambda: 80)
    eng = DraftEngine(FakeASR(["hi there"]), mt, renderer, lambda: seg.live, "zh", clock=lambda: 0.0)
    renderer.draw("EN", "hi", "there")  # a draft is on screen
    run(Frames(4), seg, FakeASR([]), mt, TextSink(out), "zh", None, draft=eng)
    s = out.getvalue()
    assert "Them final text." in s and "[ZH] FINAL:final text." in s
    # the clear escape precedes the first final line
    assert s.index("\x1b[J") < s.index("Them final text.")
    assert renderer.rows == 0


def test_segmenter_live_and_epoch():
    seg = Segmenter(chunk=2.0, use_vad=False, overlap=0.1, step=0.5)
    lives, epochs = [], []
    for s in seg.segments(Frames(5).frames()):
        epochs.append(seg.epoch)
        assert seg.live is None
    # during iteration live was set between segments; epoch bumped per segment
    assert epochs and epochs == sorted(epochs)


def test_paced_source_realtime_with_fake_clock():
    clock = Clock()
    ps = PacedSource(Frames(4), clock=clock, sleep=clock.sleep)
    list(ps.frames())
    assert abs(clock.t - 2.0) < 1e-6  # 4 x 0.5 s of audio


def test_save_jsonl_finals_only(tmp_path):
    import datetime
    import json

    from tinglongwu.sink import JsonlSaveSink, Result, default_save_path
    from tinglongwu.cli import parse_args

    clock = Clock()
    out = io.StringIO()
    p = tmp_path / "sub" / "x.jsonl"
    s = JsonlSaveSink(TextSink(out), p, clock=clock, now=lambda: datetime.datetime(2026, 1, 2, 3, 4, 5))
    clock.t = 2.5
    s.write(Result("ru", "привет", None, "你好", "zh"))
    assert p.read_text(encoding="utf-8").strip()  # flushed before close
    rec = json.loads(p.read_text(encoding="utf-8").splitlines()[0])
    assert rec == {"t": 2.5, "wall": "2026-01-02T03:04:05", "side": "them", "lang": "ru", "src": "привет", "dst": "你好"}
    assert "Them привет" in out.getvalue()
    assert default_save_path(datetime.datetime(2026, 1, 2, 3, 4, 5)) == "transcripts/20260102-030405.jsonl"
    assert parse_args([]).save is None and parse_args(["--save"]).save == "" and parse_args(["--save", "a"]).save == "a"


def test_draft_uses_final_line_labels():
    out = io.StringIO()
    r = DraftRenderer(out, columns=lambda: 80)
    r.draw("RU", "привет", "мир", "ZH", "你好")
    s = out.getvalue()
    assert "◀" in s and "→ [ZH]" in s  # same labels as fmt.block, not the old bare "[RU]"/"[ZH]" prefix
