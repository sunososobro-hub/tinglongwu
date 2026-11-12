import pytest

from tinglongwu.repeat import collapse_repeats, filter_hallucination, has_loop


@pytest.mark.parametrize("text,want", [
    ("по-по-по-по-по", "по"),
    ("ha ha ha ha ha", "ha"),
    ("la-la-la-la", "la"),
    ("и он сказал по-по-по-по-по-по потом", "и он сказал по потом"),
])
def test_loops_collapsed(text, want):
    assert collapse_repeats(text) == want
    assert filter_hallucination(text) == want


@pytest.mark.parametrize("text", [
    "very very good", "bye bye", "no no no", "да да да", "I think so-so is fine", "well-known well-known fact",
])
def test_normal_untouched(text):
    assert filter_hallucination(text) == text


def test_high_compression_ratio_dropped():
    from tinglongwu.repeat import compression_ratio
    t = "ab" * 40  # no separator: not a word/unit loop, but the compression ratio gives it away
    assert compression_ratio(t) > 2.4
    assert filter_hallucination(t) == ""


def test_normal_long_sentence_kept():
    t = "The quick brown fox jumps over the lazy dog while the band keeps playing a very very quiet tune."
    assert filter_hallucination(t) == t


def test_too_short_after_collapse_dropped():
    assert filter_hallucination("-- -- -- -- --") == ""


def test_has_loop_flags_hyphen_loop():
    assert has_loop("по-по-по-по-по-по-по-по")


def test_cjk_still_collapsed():
    assert collapse_repeats("哈哈哈哈哈哈哈哈哈") != "哈哈哈哈哈哈哈哈哈"
