"""0.3.8: speaker identity in the floating subtitles: colored prefix, async arrival, renames, no labels = unchanged."""
from tinglongwu.gui.core import SPK_PALETTE, SpeakerColors, speaker_prefix
from tinglongwu.speakers import SpeakerBook


def _fmt(n):
    return f"Speaker {n}"


def test_prefix_text_and_color_match_avatar():
    book, colors = SpeakerBook(), SpeakerColors()
    text, color = speaker_prefix(book, colors, "說話者2", lambda n: f"說話者{n}")
    assert text == "● 說話者2：" and color == colors.color("說話者2") == SPK_PALETTE[0]
    text, color = speaker_prefix(book, colors, "說話者1", _fmt)  # second speaker -> second palette color
    assert text == "● Speaker 1: " and color == SPK_PALETTE[1]


def test_no_label_no_prefix():
    book, colors = SpeakerBook(), SpeakerColors()
    assert speaker_prefix(book, colors, None, _fmt) is None
    assert speaker_prefix(book, colors, "", _fmt) is None


def test_rename_updates_text_keeps_color():
    book, colors = SpeakerBook(), SpeakerColors()
    _, c0 = speaker_prefix(book, colors, "說話者1", _fmt)
    book.rename("說話者1", "Ann")
    text, c1 = speaker_prefix(book, colors, "說話者1", _fmt)
    assert text == "● Ann: " and c1 == c0


class _Spk:
    def __init__(self):
        self.res = {}

    def result(self, tok):
        return self.res.get(tok)


class _State:
    def __init__(self, who):
        self.who = who

    def history_who(self):
        return self.who


def _app(spk, who):
    from tinglongwu.gui import app as appmod

    cls = [v for v in vars(appmod).values() if isinstance(v, type) and hasattr(v, "speaker_prefix")
           and hasattr(v, "_rename_speaker")][0]
    o = object.__new__(cls)
    o.spk, o.state, o.spk_book, o.spk_colors = spk, _State(who), SpeakerBook(), SpeakerColors()
    return o


def test_app_prefix_without_labeler_is_none():
    assert _app(None, ["spk#1"]).speaker_prefix(0) is None


def test_app_prefix_appears_when_async_label_arrives():
    spk = _Spk()
    o = _app(spk, ["spk#1", None])
    assert o.speaker_prefix(0) is None  # shown before its label is ready: plain line
    spk.res["spk#1"] = "說話者2"
    p = o.speaker_prefix(0)  # the redraw on spk_ready picks it up
    assert p and p[0].startswith("● ") and "2" in p[0] and p[1] == SPK_PALETTE[0]
    assert o.speaker_prefix(1) is None  # a line without a token (draft / mine) stays plain


def test_app_prefix_follows_rename():
    spk = _Spk()
    spk.res["spk#1"] = "說話者1"
    o = _app(spk, ["spk#1"])
    before = o.speaker_prefix(0)
    o.spk_book.rename("說話者1", "Bob")
    after = o.speaker_prefix(0)
    assert "Bob" in after[0] and after[1] == before[1]
