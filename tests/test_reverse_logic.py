import numpy as np
import pytest

from tinglongwu.reverse.logic import match_device, resample, split_sentences


def test_split_basic():
    assert split_sentences("Привет. Как дела? Хорошо!") == ["Привет.", "Как дела?", "Хорошо!"]


def test_split_cjk_and_empty():
    assert split_sentences("你好。再见！") == ["你好。", "再见！"]
    assert split_sentences("  ... ") == []
    assert split_sentences("") == []


def test_split_long_cut_at_comma():
    s = ", ".join(["слово"] * 60)
    parts = split_sentences(s, max_chars=50)
    assert len(parts) > 3 and all(len(p) <= 50 for p in parts)
    assert " ".join(parts).replace(",", "").split() == ["слово"] * 60


def test_match_device():
    names = ["Speakers (Realtek)", "CABLE Input (VB-Audio Virtual Cable)", "Headphones"]
    assert match_device(names, "cable input") == 1
    assert match_device(names, None) is None
    assert match_device(names, "") is None
    with pytest.raises(LookupError):
        match_device(names, "nothing")


def test_resample_length_and_dtype():
    x = np.sin(2 * np.pi * 440 * np.arange(24000) / 24000).astype(np.float64)
    y = resample(x, 24000, 48000)
    assert y.dtype == np.float32 and len(y) == 48000
    assert resample(x, 24000, 24000).dtype == np.float32
    assert abs(float(np.abs(y).max()) - 1.0) < 0.05
    z = resample(x, 24000, 16000)
    assert len(z) == 16000
    assert resample(np.zeros(0), 24000, 48000).size == 0


def test_cjk_filter_and_retry():
    from tinglongwu.reverse.logic import has_cjk, strip_cjk, translate_ru

    assert has_cjk("мой интернет, 请问 нужно") and not has_cjk("Привет, мир! 5 GHz")
    assert strip_cjk("Мой интернет, 请问 нужно ли?") == "Мой интернет, нужно ли?"
    calls = []

    def fake(t):
        calls.append(t)
        return "bad 请问" if len(calls) == 1 else "хорошо"

    assert translate_ru(fake, "x") == ("хорошо", 1) and len(calls) == 2
    assert translate_ru(lambda t: "ok", "x") == ("ok", 0)
    assert translate_ru(lambda t: "да 请问", "x") == ("да", 1)
