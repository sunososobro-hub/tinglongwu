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
