from tinglongwu.hallucination import is_hallucination


def test_thank_you_on_low_rms_is_dropped():
    assert is_hallucination("Thank you.", rms=0.012, no_speech=0.0)


def test_thank_you_on_normal_speech_is_kept():
    assert not is_hallucination("Thank you.", rms=0.05, no_speech=0.0)
