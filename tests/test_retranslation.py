"""#273 re-translation: whole-sentence finals, boundary de-dup, mask-k drafts, flicker count. Fakes only."""
from __future__ import annotations

import importlib.util
import io
from pathlib import Path
from types import SimpleNamespace

from tinglongwu import cli
from tinglongwu.merge import SentenceMerger, dedupe_overlap, split_clause
from tinglongwu.pipeline import run
from tinglongwu.segment import Segmenter
from tinglongwu.sink import TextSink
from tinglongwu.stream import DraftEngine, DraftRenderer, mask_tail

from test_stream import Clock, FakeASR, FakeMT, Frames, tone

_spec = importlib.util.spec_from_file_location(
    "eval_talk", Path(__file__).resolve().parent.parent / "scripts" / "eval_talk.py")
eval_talk = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(eval_talk)


def texts(units):
    return [u.text for u in units]


# ---- boundary de-dup ----
def test_dedupe_exact_and_fuzzy_seams():
    tail = "playing music engages linguistic and mathematical".split()
    assert dedupe_overlap(tail, "linguistic and mathematical precision") == "precision"
    # cut-off word at the end of the previous segment
    assert dedupe_overlap("both linguistic and mathemat".split(), "both linguistic and mathematical precision") \
        == "mathematical precision"
    assert dedupe_overlap("engages linguistic and mathemat".split(), "linguistic and mathematical precision") \
        == "linguistic and mathematical precision"  # only 2 words agree past the garbled one: too weak, kept
    # misheard word at the start of the new segment
    assert dedupe_overlap(tail, "planes linguistic and mathematical precision") == "precision"
    # unrelated text and a lone repeated word are kept
    assert dedupe_overlap(tail, "the brain lights up") == "the brain lights up"
    assert dedupe_overlap(["very"], "very good") == "very good"


def test_merger_finals_have_no_boundary_duplicates():
    m = SentenceMerger(max_wait=6, whole=True)
    lines = texts(m.add("Playing music engages both linguistic and mathemat", "en", 0, forced_cut=True))
    lines += texts(m.add("both linguistic and mathematical precision. It is a", "en", 1, forced_cut=True))
    lines += texts(m.add("is a full-body workout.", "en", 2))
    assert lines == ["Playing music engages both linguistic and mathematical precision.", "It is a full-body workout."]
    assert eval_talk.boundary_duplicates(lines) == 0 and eval_talk.mid_clause_cuts(lines) == 0


# ---- whole-sentence finals ----
def test_split_clause():
    assert split_clause("when you listen to music, your brain lights") == ("when you listen to music,",
                                                                           "your brain lights")
    assert split_clause("so, the brain is busy") == ("", "so, the brain is busy")  # head too short
    assert split_clause("no clause boundary here") == ("", "no clause boundary here")
    assert split_clause("當你聽音樂的時候，大腦會亮起來") == ("當你聽音樂的時候，", "大腦會亮起來")


def test_whole_mode_timeout_cuts_at_clause_not_mid_clause():
    m = SentenceMerger(max_wait=6, whole=True)
    m.add("when you listen to music, your brain", "en", 0)
    u = m.due(6)
    assert texts(u) == ["when you listen to music,"] and u[0].forced
    assert m.buf == "your brain" and m.deadline == 12  # the rest waits for its own sentence end
    assert texts(m.add("lights up again.", "en", 7)) == ["your brain lights up again."]
    m2 = SentenceMerger(max_wait=6, whole=True)  # no clause boundary: released whole, as before
    m2.add("no boundary at all", "en", 0)
    assert texts(m2.due(6)) == ["no boundary at all"] and m2.buf == ""
    m3 = SentenceMerger(max_wait=6)  # default (meeting without drafts) is unchanged
    m3.add("when you listen to music, your brain", "en", 0)
    assert texts(m3.due(6)) == ["when you listen to music, your brain"]


# ---- mask-k ----
def test_mask_tail_words_cjk_and_first_draft():
    assert mask_tail("hello world", "how are you", False, 2) == "hello world how"
    assert mask_tail("hello world", "how", False, 2) == "hello world"
    assert mask_tail("", "hello there", False, 2) == "hello there"  # nothing confirmed: do not delay the 1st draft
    assert mask_tail("", "a b c d", False, 2) == "a b"
    assert mask_tail("你好", "世界朋友", True, 2) == "你好世界"
    assert mask_tail("a b", "c d", False, 0) == "a b c d"


def make_masked(hyps, k=2):
    clock = Clock()
    state = {"live": (0, tone())}
    mt = FakeMT()
    eng = DraftEngine(FakeASR(hyps, "ru"), mt, DraftRenderer(io.StringIO(), columns=lambda: 80),
                      lambda: state["live"], "zh", interval=0.5, clock=clock, mask_k=k, min_gap=0.0)
    return eng, clock, state, mt


def step(eng, clock, state, n):
    clock.t += 1.0
    state["live"] = (0, tone(16000 * n))
    eng.tick()
    eng.translate_pending()


def test_draft_masks_unstable_tail_and_counts_rewrites():
    hyps = ["one two three four", "one two three fore five", "one two three four five six seven"]
    eng, clock, state, mt = make_masked(hyps)
    eng.tick()
    eng.translate_pending()
    step(eng, clock, state, 2)
    step(eng, clock, state, 3)
    # 1st: nothing confirmed -> "one two" (tail minus 2); 2nd: "one two three" agreed, tail "fore five" masked
    assert mt.drafts == ["one two", "one two three", "one two three four five"]
    assert eng.n_drafts == 3 and eng.rewrites == 0  # every draft only extended the shown text
    eng0, clock0, state0, mt0 = make_masked(hyps, k=0)
    eng0.tick()
    eng0.translate_pending()
    step(eng0, clock0, state0, 2)
    step(eng0, clock0, state0, 3)
    assert eng0.rewrites == 2  # unmasked: "four" -> "fore" -> "four" rewrote shown characters twice
    assert "draft rewrites 0.67 per draft (3 drafts, mask-k 0)" == eng0.flicker_line()


def test_draft_includes_held_merger_text():
    eng, clock, state, mt = make_masked(["is the brain's equivalent"], k=0)
    eng.held = lambda: ("Playing music is the brain's", "ru")
    eng.tick()
    eng.translate_pending()
    assert mt.drafts == ["Playing music is the brain's equivalent"]  # overlap words not repeated
    eng.held = lambda: ("other language", "en")  # a held line in another language is not mixed in
    eng._translated = ""
    eng.translation = ""
    clock.t += 1
    state["live"] = (0, tone(32000))
    eng.tick()
    eng.translate_pending()
    assert mt.drafts[-1] == "is the brain's equivalent"


# ---- pipeline + cli ----
def test_pipeline_wires_whole_mode_and_keeps_latency_lines():
    logs = []
    out = io.StringIO()
    seg = Segmenter(chunk=1.0, use_vad=False, overlap=0.1)
    mt = FakeMT()
    eng = DraftEngine(FakeASR(["hi there"]), mt, DraftRenderer(out, columns=lambda: 80), lambda: seg.live, "zh",
                      clock=lambda: 0.0, log=logs.append)
    eng.starts[0] = -1.0  # a segment whose audio started 1 s ago
    merger = SentenceMerger(max_wait=1.5)
    run(Frames(4), seg, FakeASR([]), mt, TextSink(out), "zh", merger, draft=eng, log=logs.append)
    assert merger.whole and eng.held() == (merger.buf, merger.lang)
    assert any(m.startswith("latency: final epoch=") for m in logs)
    assert logs[-1].startswith("latency: draft rewrites ")


def test_flicker_line_is_last_even_with_slow_mt():
    import time

    class SlowMT(FakeMT):
        def translate(self, text, src, tgt):
            time.sleep(0.3)
            return super().translate(text, src, tgt)

    logs = []
    out = io.StringIO()
    seg = Segmenter(chunk=1.0, use_vad=False, overlap=0.1)
    mt = SlowMT()
    eng = DraftEngine(FakeASR(["hi there"]), mt, DraftRenderer(out, columns=lambda: 80), lambda: seg.live, "zh",
                      clock=lambda: 0.0, log=logs.append)
    eng.starts[0] = -1.0
    run(Frames(4), seg, FakeASR([]), mt, TextSink(out), "zh", SentenceMerger(max_wait=1.5), draft=eng,
        log=logs.append)
    flick = [i for i, m in enumerate(logs) if m.startswith("latency: draft rewrites ")]
    finals = [i for i, m in enumerate(logs) if m.startswith("latency: final")]
    assert finals and flick and flick[-1] > max(finals)


def test_cli_raises_max_wait_with_drafts_unless_explicit(monkeypatch, tmp_path):
    import tinglongwu.asr as asr
    import tinglongwu.pipeline as pipeline
    from tinglongwu import device

    monkeypatch.setattr(device, "cuda_device_count", lambda: 0)
    monkeypatch.setattr(device, "driver_version", lambda: None)
    monkeypatch.setattr(asr, "FasterWhisperRecognizer", lambda *a, **k: SimpleNamespace(device="cuda"))
    monkeypatch.setattr(cli, "make_translator", lambda *a, **k: SimpleNamespace(preload=lambda *x: None))
    monkeypatch.setattr(cli, "select_backend", lambda *a: object())
    seen = []
    monkeypatch.setattr(pipeline, "run", lambda *a, **k: seen.append(a[6].max_wait))

    def session(argv, draft=True):
        a = cli.parse_args(["--models-dir", str(tmp_path), "--model", "small", "--lang", "en", "--no-adapt", *argv])
        cli.run_session(a, draft_factory=lambda *x: SimpleNamespace() if draft else None)
        return seen[-1]

    assert session(["--preset", "chat"]) == cli.RETRANS_MAX_WAIT == 6.0
    assert session(["--preset", "chat"], draft=False) == 1.5
    assert session(["--preset", "chat", "--max-wait", "2"]) == 2.0


# ---- #288: sentence heads, tiny fragments, 1-2 letter words before a period ----
def test_two_letter_word_ends_sentence_288():
    from tinglongwu.merge import SentenceMerger, _is_end
    assert _is_end("Please wrap it up.") and _is_end("We should do it.") and _is_end("I said so.")
    for abbr in ("Mr.", "Dr.", "т.", "г.", "He met Dr.", "see Fig. 3 vs."):
        assert not _is_end(abbr), abbr
    m = SentenceMerger(max_wait=1.5)
    out = m.add("So we wrapped it up. Then we left", "en", 0.0)
    assert [u.text for u in out] == ["So we wrapped it up."] and not out[0].forced


def test_short_head_not_released_on_pause_288():
    from tinglongwu.merge import SentenceMerger
    m = SentenceMerger(max_wait=1.5, whole=True)
    assert m.add("Well, in the", "en", 0.0, pause=1.0) == []  # pause alone does not ship a 3-word head
    out = m.add("in the last few decades, neuroscientists have studied it.", "en", 0.6)
    assert [u.text for u in out] == ["Well, in the last few decades, neuroscientists have studied it."]


def test_sentence_head_kept_after_cut_288():
    from tinglongwu.merge import SentenceMerger
    m = SentenceMerger(max_wait=1.5, whole=True)
    assert m.add("And then put it all", "en", 0.0, pause=1.0) == []
    out = m.add("And then put it all back together into unified musical experience.", "en", 0.5)
    assert [u.text for u in out] == ["And then put it all back together into unified musical experience."]


def test_short_head_released_after_double_wait_288():
    from tinglongwu.merge import SentenceMerger
    m = SentenceMerger(max_wait=1.5, whole=True)
    m.add("Well, in the", "en", 0.0)
    assert m.due(1.6) == []
    assert [u.text for u in m.due(3.1)] == ["Well, in the"]
