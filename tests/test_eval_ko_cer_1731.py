"""#1731: Korean ASR is also scored by characters (CER next to WER), so a spacing-only slip is not a whole wrong word."""
import json

import pytest

import test_eval_smoke
from test_eval_smoke import Clock, fake_engines, make_data
from tlwu_eval import langmatrix, scoring
from tlwu_eval.__main__ import format_report, main

KO_REF = "고양이가 매트 위에 앉았다"
KO_SPACING_ONLY = "고양이가 매트위에 앉았 다"  # same characters, different spaces


def test_spacing_only_difference_is_cer_zero_but_wer_above_zero():
    pairs = [(KO_REF, KO_SPACING_ONLY)]
    assert scoring.error_rate(pairs, "ko") > 0  # WER: "매트위에" / "앉았" / "다" are wrong words
    assert scoring.error_rate(pairs, "ko", chars=True) == 0.0
    # one real character slip: CER = 1 / 11 characters, WER = 1 / 4 words
    one = [(KO_REF, "고양이가 매트 위에 앉았어")]
    assert scoring.error_rate(one, "ko", chars=True) == pytest.approx(1 / 11)
    assert scoring.error_rate(one, "ko") == pytest.approx(1 / 4)


def test_chars_flag_does_not_change_char_languages():
    pairs = [("猫がマットの上に座った", "猫がマットの上で座った")]
    assert scoring.error_rate(pairs, "ja", chars=True) == scoring.error_rate(pairs, "ja")


def _run(tmp_path, monkeypatch, pair, asr_text=None):
    monkeypatch.setitem(test_eval_smoke.REFS, "ko", (KO_REF, "貓坐在墊子上"))
    data = tmp_path / "d"
    data.mkdir()
    make_data(data, langs=("ko", "en"), n=3)
    out = tmp_path / "r.json"
    clock = Clock()
    main(["--smoke", "--data", str(data), "--pairs", pair, "--clips", "3", "--no-noise", "--out", str(out)],
         engines=fake_engines(clock, asr_text=asr_text), totals=(16000, None), clock=clock)
    return json.loads(out.read_text("utf-8"))


def test_ko_pair_reports_and_prints_cer_next_to_wer(tmp_path, monkeypatch):
    rep = _run(tmp_path, monkeypatch, "ko->zh", asr_text=KO_SPACING_ONLY)
    lg = rep["languages"][0]
    assert lg["error_rate"] > 0 and lg["cer"] == 0.0
    ok, line = langmatrix.summary_line(rep, "ko->zh")
    assert ok and f"ASR error rate {lg['error_rate'] * 100:.2f}%, CER 0.00%, chrF" in line
    assert "WER " in format_report(rep) and "CER 0.0%" in format_report(rep)


def test_other_word_languages_keep_the_wer_only_line(tmp_path, monkeypatch):
    rep = _run(tmp_path, monkeypatch, "en->zh")
    assert rep["languages"][0]["cer"] is None
    ok, line = langmatrix.summary_line(rep, "en->zh")
    assert ok and "CER" not in line and "3 clips, ASR error rate 0.00%, chrF 100.00" in line
    assert "CER" not in format_report(rep)
