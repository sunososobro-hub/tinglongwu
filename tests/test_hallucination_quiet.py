from tinglongwu.hallucination import is_hallucination


def test_thank_you_on_low_rms_is_dropped():
    assert is_hallucination("Thank you.", rms=0.012, no_speech=0.0)


def test_thank_you_on_normal_speech_is_kept():
    assert not is_hallucination("Thank you.", rms=0.05, no_speech=0.0)


def test_ru_thanks_for_subtitles_variants_are_dropped():
    for s in ("Спасибо за субтитры", "Спасибо за субтитры.", "СПАСИБО ЗА СУБТИТРЫ!", "  спасибо, за субтитры… "):
        assert is_hallucination(s, rms=0.05, no_speech=0.0), s
    assert not is_hallucination("Спасибо за обед", rms=0.05, no_speech=0.0)
