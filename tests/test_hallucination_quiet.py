from tinglongwu.hallucination import is_hallucination


def test_thank_you_on_low_rms_is_dropped():
    assert is_hallucination("Thank you.", rms=0.012, no_speech=0.0)


def test_thank_you_on_normal_speech_is_kept():
    assert not is_hallucination("Thank you.", rms=0.05, no_speech=0.0)


def test_ru_thanks_for_subtitles_variants_are_dropped():
    for s in ("Спасибо за субтитры", "Спасибо за субтитры.", "СПАСИБО ЗА СУБТИТРЫ!", "  спасибо, за субтитры… "):
        assert is_hallucination(s, rms=0.05, no_speech=0.0), s
    assert not is_hallucination("Спасибо за обед", rms=0.05, no_speech=0.0)


def test_yoyo_silence_credit_is_dropped_even_at_normal_energy_issue_759():
    for s in ("優優獨播劇場——YoYo Television Series Exclusive", "优优独播剧场", "YoYo Television Series Exclusive"):
        assert is_hallucination(s, rms=0.05, no_speech=0.0), s


def test_speak_gate_rejects_yoyo_line_issue_759():
    from tinglongwu.reverse.gate import SegmentStats, gate

    ok, why = gate(SegmentStats(2.0, 0.05, "優優獨播劇場——YoYo Television Series Exclusive"))
    assert (ok, why) == (False, "repeat")
    assert gate(SegmentStats(2.0, 0.05, "我們明天開會討論預算"))[0]


def test_generic_drama_theatre_sentence_is_kept_issue_759():
    assert not is_hallucination("独播剧场今晚播出新剧", rms=0.05, no_speech=0.0)
