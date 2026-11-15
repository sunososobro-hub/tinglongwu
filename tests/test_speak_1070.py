"""#1070: speak leg pinned to My language, empty/wrong-script/repeat hallucinations dropped, Traditional original, hugging bubbles."""
import queue
from types import SimpleNamespace

import numpy as np
import pytest

from tinglongwu.gui.core import SpeakQueueSink, both_settings, wrap_bubble
from tinglongwu.repeat import collapse_repeats
from tinglongwu.reverse.gate import SegmentStats, gate, segment_gate
from tinglongwu.sink import Result

GOOD = dict(duration=1.5, rms=0.05, text="我先把東西放在你底下", no_speech=0.05, avg_logprob=-0.3)


def test_speak_leg_asr_pinned_to_my_language():
    from tinglongwu.gui.core import DEFAULTS, settings_to_argv

    listen, speak = both_settings({**DEFAULTS, "target": "zh-tw", "lang": "en"})
    assert speak["lang"] == "zh" and speak["target"] == "en"
    assert settings_to_argv(speak)[:2] == ["--lang", "zh"]
    assert settings_to_argv(listen)[:2] == ["--lang", "en"]  # listen leg: the Other side language


@pytest.mark.parametrize("text", ["Walk in the park.", "Today it's a nice day.", "Kissing began as a flap."])
def test_english_text_on_a_chinese_pinned_leg_is_dropped(text):
    assert gate(SegmentStats(**{**GOOD, "text": text}), lang="zh") == (False, "wrong_lang")
    assert gate(SegmentStats(**{**GOOD, "text": text}), lang="en") == (True, "")  # an English leg keeps it
    assert gate(SegmentStats(**GOOD), lang="zh") == (True, "")


def test_empty_source_never_passes():
    assert gate(SegmentStats(**{**GOOD, "text": "   "})) == (False, "empty")
    assert gate(SegmentStats(**{**GOOD, "text": ""})) == (False, "empty")


def test_unsure_and_probably_noise_is_dropped():
    assert gate(SegmentStats(**{**GOOD, "no_speech": 0.5, "avg_logprob": -0.9})) == (False, "low_logprob")
    assert gate(SegmentStats(**{**GOOD, "no_speech": 0.5, "avg_logprob": -0.3}))[0]


def test_segment_gate_uses_lang_and_reports_the_drop():
    dropped = []
    g = segment_gate(lambda text, why: dropped.append((text, why)), report=SimpleNamespace(add=lambda w: None), lang="zh")
    seg = np.full(16000, 0.1, dtype=np.float32)
    assert g(seg, SimpleNamespace(text="Walk in the park.", no_speech=0.0, avg_logprob=-0.2)) is False
    assert dropped == [("Walk in the park.", "wrong_lang")]
    assert g(seg, SimpleNamespace(text="我先把東西放在你底下", no_speech=0.0, avg_logprob=-0.2)) is True


@pytest.mark.parametrize("text", [
    "我先把東西放在你底下我先把東西放在你底下我先把東西放在你底下",
    "我先把東西放在你底下，我先把東西放在你底下，我先把東西放在你底下。",
    "我先把東西放在你底下 我先把東西放在你底下 我先把東西放在你底下",
])
def test_repeated_clause_collapses_to_one(text):
    out = collapse_repeats(text)
    assert out.count("我先把東西放在你底下") == 1


def test_real_reduplication_is_kept():
    assert collapse_repeats("對對對 謝謝 好好") == "對對對 謝謝 好好"


def test_speak_original_shown_in_traditional():
    q = queue.Queue()
    SpeakQueueSink(q).write(Result(src="zh", original="你听得到吗", pivot=None, target="Can you hear me?", tgt="en"))
    ev = q.get_nowait()
    assert ev[0] == "final_side" and "你聽得到嗎" in ev[1] and "听" not in ev[1]


def test_wrap_bubble_limits_width_and_splits_cjk():
    m = len  # one pixel per character
    lines = wrap_bubble("hello there this is a longer text that wraps", m, 15)
    assert all(m(x) <= 15 for x in lines) and " ".join(lines) == "hello there this is a longer text that wraps"
    cjk = wrap_bubble("我先把東西放在你底下" * 3, m, 8)
    assert all(m(x) <= 8 for x in cjk) and "".join(cjk) == "我先把東西放在你底下" * 3
    assert wrap_bubble("", m, 10) == [""]
    assert wrap_bubble("a" * 40, m, 10) == ["a" * 10] * 4


def test_bubble_background_never_covers_margin_or_newline(tmp_path, monkeypatch):
    tk = pytest.importorskip("tkinter")
    try:
        tk.Tk().destroy()
    except tk.TclError:
        pytest.skip("no display")
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from tinglongwu.gui import app

    a = app.App()
    try:
        a.state.apply(("final_side", "🎤 我說中文", "🎤 " + "I speak Chinese quite a lot today " * 8))
        a.render_log()
        tx = a.log_text
        r = tx.tag_ranges("me_final")
        assert len(r) >= 4  # long text is wrapped into several bubble lines
        for i in range(0, len(r), 2):
            line_start = tx.index(f"{r[i]} linestart")
            assert tx.get(line_start, r[i]) == " "  # the first char of the line is a plain spacer, not the bubble
            assert not tx.get(r[i], r[i + 1]).endswith("\n")
            assert "me_final" not in tx.tag_names(line_start)
    finally:
        a.root.destroy()
