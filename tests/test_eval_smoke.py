"""#101: eval smoke runner end to end on a tiny fixture with fake engines (real wav files, fake clock)."""
import json
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf

from tlwu_eval import runner

REFS = {"en": ("the cat sat on the mat", "貓坐在墊子上"), "ja": ("猫がマットの上に座った", "貓坐在墊子上"),
        "ru": ("кошка сидела на коврике", "貓坐在墊子上"), "zh": ("貓坐在墊子上", "the cat sat on the mat")}


def make_data(tmp_path, langs=("en", "ja", "ru", "zh"), n=6):
    rows = []
    for lang in langs:
        for i in range(n):
            name = f"{lang}_{i}.wav"
            sf.write(tmp_path / name, np.zeros(32000, np.float32), 16000)  # 2 s
            tgt = "en" if lang == "zh" else "zh"
            rows.append({"id": f"{lang}_{i}", "lang": lang, "wav": name, "ref_text": REFS[lang][0],
                         f"ref_{tgt}": REFS[lang][1]})
    (tmp_path / "manifest.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), "utf-8")
    return tmp_path


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def fake_engines(clock, asr_s=0.4, mt_s=0.2, tts=None, asr_text=None, mt_text=None):
    class Asr:
        def __init__(self, lang):
            self.lang = lang
            clock.t += 1.0  # model load

        def transcribe(self, audio):
            assert audio.dtype == np.float32 and len(audio) == 32000
            clock.t += asr_s
            text = REFS[self.lang][0] if asr_text is None else asr_text
            return SimpleNamespace(text=text, lang=self.lang) if text else None

    class Mt:
        def translate(self, text, src, tgt):
            clock.t += mt_s
            return SimpleNamespace(text=REFS[src][1] if mt_text is None else mt_text)

    return runner.Engines(load_asr=Asr, load_mt=Mt, tts=tts)


def smoke(tmp_path, clock, eng, **kw):
    rows = runner.load_manifest(make_data(tmp_path))
    return runner.run_smoke(rows, eng, clock=clock, ram=lambda: 2000.0, vram=lambda: None, totals=(16000, None), **kw)


def test_smoke_report_per_stage_times_and_pass(tmp_path):
    clock = Clock()
    rep = smoke(tmp_path, clock, fake_engines(clock))
    assert rep["pass"] and [lg["pair"] for lg in rep["languages"]] == ["en->zh", "ja->zh", "ru->zh", "zh->en"]
    en = rep["languages"][0]
    assert len(en["clips"]) == 5  # 6 in the manifest, smoke takes 5 per language
    assert en["seconds"] == {"load": 1.0, "asr": pytest.approx(2.0), "mt": pytest.approx(1.0), "tts": None}
    assert en["asr_rtf"] == pytest.approx(0.2) and en["mt_median_s"] == pytest.approx(0.2)  # 0.4 s per 2 s clip
    assert en["stages"] == {"asr": "PASS", "mt": "PASS", "tts": "SKIP", "time": "PASS"}
    assert en["error_rate"] == 0 and en["chrf"] == 100.0 and en["clips"][0]["latency_s"] == pytest.approx(0.6)
    assert rep["peak_ram_mib"] == 2000 and rep["peak_vram_mib"] is None
    sc = rep["score"]
    assert sc["metrics"]["asr"]["score"] == 100 and sc["metrics"]["latency"]["score"] == 100
    assert sc["metrics"]["resources"]["score"] == 100 and sc["metrics"]["lang_id"]["score"] == 100
    assert sc["missing"] == ["hallucination"]


def test_slow_asr_and_slow_mt_fail_their_stage(tmp_path):
    clock = Clock()
    rep = smoke(tmp_path, clock, fake_engines(clock, asr_s=2.5, mt_s=1.5))  # RTF 1.25, 1.5 s/sentence
    assert not rep["pass"]
    assert all(lg["stages"]["asr"] == "FAIL" and lg["stages"]["mt"] == "FAIL" for lg in rep["languages"])
    assert rep["score"]["metrics"]["latency"]["score"] == 0  # 4 s per line


def test_empty_output_fails_and_over_budget_fails(tmp_path):
    clock = Clock()
    rep = smoke(tmp_path, clock, fake_engines(clock, asr_text=""))
    assert not rep["pass"] and rep["languages"][0]["stages"]["asr"] == "FAIL"
    assert rep["languages"][0]["error_rate"] == 1.0
    clock = Clock()
    eng = fake_engines(clock)
    slow_load = eng.load_asr

    def load(lang):
        clock.t += 130  # one language alone takes more than its 2 minutes
        return slow_load(lang)

    eng.load_asr = load
    rep = runner.run_smoke(runner.load_manifest(tmp_path), eng, clock=clock, ram=lambda: None, vram=lambda: None)
    assert not rep["pass"] and rep["languages"][0]["stages"]["time"] == "FAIL"
    assert rep["languages"][0]["stages"]["asr"] == "PASS" and "resources" in rep["score"]["missing"]


def test_tts_stage_is_timed_when_installed(tmp_path):
    clock = Clock()

    def tts(text, lang):
        clock.t += 0.5
        return 2.0  # seconds of speech produced

    rep = smoke(tmp_path, clock, fake_engines(clock, tts=tts))
    en = rep["languages"][0]
    assert en["seconds"]["tts"] == pytest.approx(2.5) and en["tts_rtf"] == pytest.approx(0.25)
    assert en["stages"]["tts"] == "PASS"


def test_manifest_errors_are_clear(tmp_path):
    with pytest.raises(FileNotFoundError, match="manifest.jsonl is missing"):
        runner.load_manifest(tmp_path / "nope")
    (tmp_path / "manifest.jsonl").write_text('{"id": "a", "lang": "en"}\n', "utf-8")
    with pytest.raises(ValueError, match="missing wav, ref_text"):
        runner.load_manifest(tmp_path)
