"""#389: a very short finished sentence is not translated alone; it is joined to a neighbour within max-wait."""
from tinglongwu import cli
from tinglongwu.merge import SentenceMerger


def texts(units):
    return [u.text for u in units]


def test_fragment_held_and_merged_with_next():
    m = SentenceMerger(max_wait=6, min_words=3)
    assert texts(m.add("Давайте попробуем вклинить сюда.", "ru", 0)) == ["Давайте попробуем вклинить сюда."]
    assert m.add("Максима Чижова.", "ru", 2) == []  # the evidence fragment is not released alone
    assert m.deadline == 8
    out = m.add("Он сейчас подключится к звонку.", "ru", 4)
    assert texts(out) == ["Максима Чижова. Он сейчас подключится к звонку."] and not out[0].forced
    assert m.buf == "" and m.deadline is None


def test_fragment_merged_with_previous_in_same_segment():
    m = SentenceMerger(max_wait=6, min_words=3)
    assert texts(m.add("Давайте попробуем вклинить сюда. Максима Чижова.", "ru", 0)) == \
        ["Давайте попробуем вклинить сюда. Максима Чижова."]
    assert m.buf == ""


def test_fragment_before_unfinished_text_waits_with_it():
    m = SentenceMerger(max_wait=6, min_words=3)
    assert m.add("Да. И потом", "ru", 0) == []
    assert texts(m.add("мы пошли домой.", "ru", 1)) == ["Да. И потом мы пошли домой."]


def test_no_merge_when_max_wait_is_hit():
    m = SentenceMerger(max_wait=6, min_words=3)
    assert m.add("Максима Чижова.", "ru", 10) == []
    assert m.due(15.9) == []
    u = m.due(16.0)
    assert texts(u) == ["Максима Чижова."] and u[0].forced
    # a later sentence is released on its own
    assert texts(m.add("Он сейчас подключится к звонку.", "ru", 17)) == ["Он сейчас подключится к звонку."]


def test_whole_mode_fragment_never_waits_past_max_wait():
    m = SentenceMerger(max_wait=6, min_words=3, whole=True)
    m.add("Максима Чижова.", "ru", 0)
    assert texts(m.due(6.0)) == ["Максима Чижова."]
    m2 = SentenceMerger(max_wait=6, min_words=3, whole=True)  # a held fragment followed by a short head
    m2.add("Хорошо.", "ru", 0)
    m2.add("И", "ru", 1)
    assert m2.due(5.9) == []
    assert texts(m2.due(6.0)) == ["Хорошо. И"]


def test_cjk_characters_count_as_words():
    m = SentenceMerger(max_wait=6, min_words=3)
    assert m.add("好。", "zh", 0) == []  # 1 unit: fragment
    assert texts(m.add("我們走吧。", "zh", 1)) == ["好。我們走吧。"]  # joined without a space
    m2 = SentenceMerger(max_wait=6, min_words=3)
    assert texts(m2.add("我們走吧。", "zh", 0)) == ["我們走吧。"]  # 4 units: not a fragment
    m3 = SentenceMerger(max_wait=6, min_words=3)
    assert m3.add("はい。", "ja", 0) == []
    assert texts(m3.add("行きましょう。", "ja", 1)) == ["はい。行きましょう。"]


def test_latin_and_off_by_default():
    m = SentenceMerger(max_wait=6, min_words=3)
    assert m.add("Yes.", "en", 0) == []
    assert texts(m.add("Let us start the meeting.", "en", 1)) == ["Yes. Let us start the meeting."]
    off = SentenceMerger(max_wait=6)  # min_words defaults to 0: unchanged behaviour
    assert texts(off.add("Yes.", "en", 0)) == ["Yes."]


def test_cli_min_words_per_preset_and_override():
    assert cli.parse_args([]).min_words == 3  # meeting is the default preset
    assert cli.parse_args(["--preset", "chat"]).min_words == 0
    assert cli.parse_args(["--preset", "chat", "--min-words", "2"]).min_words == 2
    assert cli.parse_args(["--min-words", "0"]).min_words == 0
