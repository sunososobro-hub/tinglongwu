"""0.3.8: colored speaker avatars (palette, first-seen stability, initial after rename, no label -> no avatar)."""
from tinglongwu.gui.core import SPK_PALETTE, SpeakerColors, speaker_initial
from tinglongwu.speakers import SpeakerBook


def test_palette_is_6_to_8_distinct_hues():
    assert 6 <= len(SPK_PALETTE) <= 8 and len(set(SPK_PALETTE)) == len(SPK_PALETTE)


def test_colors_first_seen_order_and_stable():
    c = SpeakerColors()
    a, b = c.color("說話者1"), c.color("說話者2")
    assert (a, b) == SPK_PALETTE[:2] and a != b
    assert c.color("說話者2") == b and c.color("說話者1") == a
    for i in range(3, 12):  # more speakers than colors: wraps, never fails
        assert c.color(f"說話者{i}") in SPK_PALETTE


def test_initial_number_then_name_and_color_kept_on_rename():
    book, c = SpeakerBook(), SpeakerColors()
    before = c.color("說話者1")
    assert speaker_initial(book, "說話者1") == "1"
    book.rename("說話者1", "alice")
    assert speaker_initial(book, "說話者1") == "A"
    assert c.color("說話者1") == before


def test_no_label_no_avatar():
    assert speaker_initial(SpeakerBook(), "") == "" and speaker_initial(SpeakerBook(), None) == ""


class _FakeText:
    def __init__(self):
        self.out = []

    def index(self, *_a):
        return "1.0"

    def configure(self, **_k):
        pass

    def delete(self, *_a):
        pass

    def insert(self, _where, s, *_tags):
        self.out.append(s)

    def tag_configure(self, *_a, **_k):
        pass

    def tag_bind(self, *_a, **_k):
        pass

    def yview(self, *_a):
        pass


def _header(label):
    """Run App.render_log on a fake window with one 'other' bubble; return the header line."""
    from types import SimpleNamespace

    from tinglongwu.gui.app import App

    tx = _FakeText()
    app = SimpleNamespace(
        log_text=tx, state=SimpleNamespace(history=lambda: [("src", "hi", False)]), settings={},
        spk_colors=SpeakerColors(), spk_book=SpeakerBook(), _spk_tags_bound=set(),
        _log_at_end=lambda: False, _log_to_end=lambda: None, placeholder=lambda: None,
        _speaker_label=lambda _i: label, _insert_bubble=lambda *a, **k: None, _rename_speaker=lambda r: None)
    App.render_log(app)
    return "".join(tx.out).split("\n")[0]


def test_labelled_header_shows_name_not_other_side_word():
    from tinglongwu.i18n import t

    head = _header(("說話者1", "說話者 1"))
    assert "說話者 1" in head and head.strip().startswith("1") and t("gui_chat_other") not in head


def test_unlabelled_header_keeps_other_side_word():
    from tinglongwu.i18n import t

    assert _header(None) == t("gui_chat_other")
