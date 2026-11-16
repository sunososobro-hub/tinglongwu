"""#1257: user glossary for technical terms (ASR hotwords + MT) and its with/without eval (scripts/bench/term_eval.py)."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tinglongwu.glossary import Glossary, parse

REPO = Path(__file__).resolve().parents[1]


def _term_eval():
    spec = importlib.util.spec_from_file_location("term_eval", REPO / "scripts" / "bench" / "term_eval.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


TE = _term_eval()


# ---- glossary: short Latin terms match whole words only ----

def test_short_latin_terms_do_not_fire_on_longer_english_words():
    g = Glossary(parse("SoC = SoC ~ 賽季\nPHY\nWeb\n"))
    assert g.relevant("our social plan is physical, see the website") == []
    assert g.prompt_block("the social season") == ""


def test_short_latin_terms_match_plain_plurals_but_not_longer_words():
    g = Glossary(parse("SoC = 晶片\nPHY\nLTE\nWeb\nBox\n2 = 二\n"))
    assert [e.main for e in g.relevant("Новые SoCs от Qualcomm")] == ["SoC"]
    assert [e.main for e in g.relevant("два PHYs на плате")] == ["PHY"]
    assert [e.main for e in g.relevant("модемы LTEs")] == ["LTE"]
    assert [e.main for e in g.relevant("Этот SoC")] == ["SoC"]
    assert [e.main for e in g.relevant("two Boxes")] == ["Box"]
    assert g.relevant("social physical website 2s") == []


def test_wrong_rendering_fix_needs_the_real_term_not_a_prefix():
    g = Glossary(parse("SoC = SoC ~ 賽季\n"))
    # "season" really is 賽季 here: "social" must not turn it into SoC
    assert g.fix_output("這個賽季的社交計畫", "the social season") == "這個賽季的社交計畫"
    assert g.fix_output("這個賽季很熱", "this SoC runs hot") == "這個SoC很熱"


def test_short_latin_terms_still_match_whole_words_and_hyphen_parts():
    g = Glossary(parse("SoC = SoC\nPHY\nWeb\nLTE\n"))
    found = [e.main for e in g.relevant("SoC-based board, the PHY layer, Web-интерфейс, модем LTE")]
    assert found == ["SoC", "PHY", "Web", "LTE"]


def test_long_and_cyrillic_terms_keep_prefix_matching():
    g = Glossary(parse("router = 路由器\nчастота = 頻率\n"))
    assert [e.main for e in g.relevant("two routers на частоте")] == ["router", "частота"]


# ---- eval set ----

def test_eval_glossary_has_20_terms_and_all_fit_the_whisper_hotword_budget():
    g = Glossary(parse(TE.glossary_text()))
    assert len(g.entries) == 20
    hot = g.hotwords()
    assert hot.split(", ") == [t[0] for t in TE.TERMS]
    soc = next(e for e in g.entries if e.main == "SoC")
    assert soc.target == "SoC" and "賽季" in soc.wrong


def test_eval_set_has_two_clips_per_term_plus_distractors_without_terms():
    rows = TE.clips()
    terms = [r for r in rows if r["term"]]
    assert len(terms) == 40 and len({r["id"] for r in rows}) == len(rows)
    for r in terms:
        assert TE.asr_hit(r["term"], r["ref_text"]), r  # the written sentence has the term
        assert r["term"] not in r["say"]  # the TTS says it the Russian way, not spelled in Latin
    for r in rows:
        if not r["term"]:
            assert TE.inserted(r["ref_text"]) == []


@pytest.mark.parametrize("term,text,hit", [
    ("SoC", "Этот SoC сильно греется.", True),
    ("SoC", "Этот сок сильно греется.", False),
    ("SoC", "social", False),
    ("Web", "через WEP интерфейс", False),
    ("PCIe", "по шине PCI Express", True),
    ("U-Boot", "Загрузчик U-Boot не видит", True),
    ("U-Boot", "Загрузчик uboot не видит", True),
])
def test_asr_hit(term, text, hit):
    assert TE.asr_hit(term, text) is hit


def test_zh_hit_needs_rendering_and_no_known_wrong_one():
    assert TE.zh_hit("SoC", "這個SoC在負載下很熱。")
    assert not TE.zh_hit("SoC", "這個賽季在負載下很熱。")
    assert TE.zh_hit("Qualcomm", "高通的晶片支援兩個頻段。")
    assert not TE.zh_hit("Qualcomm", "Qualcomm的晶片支援兩個頻段。")


class _FakeAsr:
    """Hears the term only with hotwords; with them it also turns "мимо" into MIMO (a false insertion)."""

    def __init__(self):
        self.glossary, self._last, self.hot_seen = None, "stale", []

    def transcribe(self, x):
        self.hot_seen.append((self.glossary.hotwords() if self.glossary else None, self._last))
        r = x["row"]
        if not self.glossary:
            return SimpleNamespace(text=r["say"])
        return SimpleNamespace(text=r["ref_text"].replace("мимо", "MIMO"))


class _FakeMt:
    def __init__(self):
        self.glossary, self.history = None, ["ctx"]

    def translate(self, text, src, tgt):
        assert (src, tgt) == ("ru", "zh") and self.history == []
        out = "賽季" if "SoC" in text and not self.glossary else text
        if self.glossary:
            for e in self.glossary.relevant(text):
                out = out + e.target
        return SimpleNamespace(text=out)


def test_run_and_summarize_measure_with_vs_without(tmp_path):
    rows = [r for r in TE.clips() if r["term"] in ("SoC", "Qualcomm")] + [
        r for r in TE.clips() if r["ref_text"].startswith("Мы прошли мимо")]
    g = Glossary(parse(TE.glossary_text()))
    asr, mt = _FakeAsr(), _FakeMt()
    res = TE.run_clips(rows, asr, mt, g, _Audio)
    assert all(last == "" for _, last in asr.hot_seen)  # previous-text prompt reset each pass
    assert [h is None for h, _ in asr.hot_seen[:2]] == [True, False]
    s = TE.summarize(res)
    assert s["term_clips"] == 4 and s["distractor_clips"] == 1
    assert s["no"]["asr_term_acc"] == 0.0 and s["gl"]["asr_term_acc"] == 1.0
    assert s["no"]["zh_gold_term_acc"] == 0.0 and s["gl"]["zh_gold_term_acc"] == 1.0
    assert s["no"]["false_insertions"] == 0 and s["gl"]["false_insertions"] == 1
    assert s["per_term"]["SoC"] == {"asr_no": 0, "asr_gl": 2, "zh_no": 0, "zh_gl": 2, "clips": 2}
    out = tmp_path / "out"
    out.mkdir()
    (out / "terms.jsonl").write_text("".join(json.dumps(d, ensure_ascii=False) + "\n" for d in res), "utf-8")
    assert TE.main(["report", str(out)]) == 0
    md = TE.table(json.loads((out / "summary.json").read_text("utf-8")))
    assert "| ASR term accuracy | 0.0% | 100.0% | +100.0 pt |" in md
    assert "| false term insertions (distractors) | 0 | 1 | +1 |" in md


class _Audio(dict):
    def __init__(self, r):
        super().__init__(row=r)

    def __len__(self):
        return 16000


def test_synth_wav_writer_round_trips(tmp_path):
    import wave

    np = pytest.importorskip("numpy")
    p = tmp_path / "x.wav"
    TE.write_wav(p, np.array([0.0, 0.5, -2.0], dtype=np.float32), 22050)
    with wave.open(str(p)) as w:
        assert (w.getframerate(), w.getnchannels(), w.getnframes()) == (22050, 1, 3)
        frames = np.frombuffer(w.readframes(3), dtype="<i2")
    assert frames.tolist() == [0, 16383, -32767]
