"""#57 part 1: eval scoring functions give the expected scores on fixed inputs."""
import pytest

from tlwu_eval import scoring as sc


def test_weights_follow_the_owner_table_and_sum_to_100():
    assert sc.WEIGHTS == {"asr": 25, "mt": 25, "latency": 20, "hallucination": 15, "resources": 10, "lang_id": 5}
    assert sum(sc.WEIGHTS.values()) == 100


def test_error_rate_wer_for_words_cer_for_zh_ja():
    # 1 substitution in 6 words; punctuation and case do not count
    assert sc.error_rate([("The cat sat on the mat.", "the cat sat on a mat")], "en") == pytest.approx(1 / 6)
    # corpus level: (1 + 0) edits / (6 + 2) words
    assert sc.error_rate([("The cat sat on the mat.", "the cat sat on a mat"), ("привет мир", "Привет, мир!")],
                         "en") == pytest.approx(1 / 8)
    # zh by characters: 1 wrong of 4, spaces ignored
    assert sc.error_rate([("今天天氣", "今天 天器")], "zh") == pytest.approx(0.25)
    assert sc.error_rate([("こんにちは", "")], "ja") == 1.0
    with pytest.raises(ValueError):
        sc.error_rate([("", "x")], "en")


def test_chrf_known_values():
    assert sc.chrf([("今天天氣很好", "今天天氣很好")]) == pytest.approx(100.0)
    assert sc.chrf([("abc", "xyz")]) == 0.0
    assert sc.chrf([("abc", "")]) == 0.0
    # by hand: 1-grams F=2/3, 2-grams F=1/2, 3-grams F=0, longer orders do not exist -> mean of three
    assert sc.chrf([("abc", "abd")]) == pytest.approx(100 * (2 / 3 + 1 / 2 + 0) / 3)
    assert sc.chrf([("a b c", "abc")]) == pytest.approx(100.0)  # whitespace ignored
    # recall weighs more than precision (beta = 2): dropping half is worse than adding as much again
    assert sc.chrf([("abcdefgh", "abcd")]) < sc.chrf([("abcd", "abcdefgh")])
    better = sc.chrf([("今天天氣很好，我們去公園散步。", "今天天氣很好，我們去公園走走。")])
    worse = sc.chrf([("今天天氣很好，我們去公園散步。", "明天可能會下雨。")])
    assert 0 < worse < better < 100


def test_percentile_is_nearest_rank():
    v = [0.5, 1, 2, 3, 4, 5, 6, 7, 8, 30]
    assert sc.percentile(v, 90) == 8 and sc.percentile(v, 100) == 30 and sc.percentile(v, 50) == 4
    assert sc.percentile([2.5], 90) == 2.5


def test_asr_and_mt_scores():
    assert sc.asr_score(0.0) == 100 and sc.asr_score(0.07) == pytest.approx(93) and sc.asr_score(1.4) == 0
    assert sc.mt_score(55.5) == 55.5 and sc.mt_score(130) == 100


def test_latency_1s_is_100_and_4s_is_0():
    assert sc.latency_score([1.0, 1.0, 0.4]) == 100
    assert sc.latency_score([4.0, 5.0, 9.0]) == 0
    assert sc.latency_score([2.5] * 10) == pytest.approx(50)
    lat = [1.0] * 8 + [4.0, 4.0]
    assert sc.latency_score(lat) == pytest.approx(0.5 * sc.linear(1.6, 1, 4) + 0.5 * 0)


def test_hallucination_counts_clips_with_any_text():
    assert sc.hallucination_score(["", "  ", None, ""]) == 100
    assert sc.hallucination_score(["", "Thank you.", "", "字幕由Amara提供"]) == 50
    with pytest.raises(ValueError):
        sc.hallucination_score([])


def test_resources_worst_of_ram_and_vram():
    assert sc.resources_score(4000, 16000) == 100  # 25 % of RAM
    assert sc.resources_score(12000, 16000) == pytest.approx(50)  # 75 %
    assert sc.resources_score(16000, 16000) == 0
    assert sc.resources_score(4000, 16000, 7372.8, 8192) == pytest.approx(20)  # VRAM 90 % decides
    assert sc.resources_score(4000, 16000, None, None) == 100  # CPU-only machine


def test_lang_id_and_colours():
    assert sc.lang_id_score([("ja", "ja"), ("ja", "zh"), ("en", "en"), ("ru", "ru")]) == 75
    assert [sc.colour(x) for x in (100, 80, 79.9, 60, 59.9, 0)] == ["green", "green", "yellow", "yellow", "red", "red"]


def test_overall_weighted_and_renormalised_when_a_metric_is_missing():
    full = sc.overall({"asr": 90, "mt": 80, "latency": 50, "hallucination": 100, "resources": 100, "lang_id": 60})
    assert full["overall"] == pytest.approx((25 * 90 + 25 * 80 + 20 * 50 + 15 * 100 + 10 * 100 + 5 * 60) / 100)
    assert full["colour"] == "green" and full["missing"] == []
    assert full["metrics"]["latency"] == {"score": 50.0, "weight": 20, "colour": "red"}
    part = sc.overall({"asr": 90, "mt": 50, "hallucination": None})
    assert part["overall"] == 70 and part["colour"] == "yellow"
    assert part["missing"] == ["hallucination", "lang_id", "latency", "resources"]
    with pytest.raises(ValueError):
        sc.overall({"tts": 50})
    with pytest.raises(ValueError):
        sc.overall({"asr": None})


def test_kana_guard_flags_zh_outputs_with_kana():
    outs = ["今天天氣很好", "これは日本語です", "我去買ケーキ"]
    assert sc.kana_rate(outs) == pytest.approx(66.67)
    assert sc.kana_colour(sc.kana_rate(outs)) == "red"
    clean = sc.kana_rate(["今天天氣很好", "謝謝"])
    assert clean == 0 and sc.kana_colour(clean) == "green"
    with pytest.raises(ValueError):
        sc.kana_rate([])
