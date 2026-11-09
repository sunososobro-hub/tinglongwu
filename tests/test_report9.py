import numpy as np
import pytest

from tests.test_report5 import FakeSeg, FakeModel
from tinglongwu import asr, cli, glossary, qwen
from tinglongwu.repeat import collapse_repeats, has_loop

UNIT = "Если ваш роутер работает на частоте 5,4 ГГц, то"
# exact text from the live ru_03 transcript (t=144.44): 13 copies + a cut-off tail
LOOP = " ".join([UNIT] + [UNIT.replace("Если", "если")] * 12) + " если ваш"
EXAMPLE = "docs/examples/glossary_wifi_ru.txt"


def test_collapse_exact_live_loop():
    assert has_loop(LOOP)
    assert collapse_repeats(LOOP) == UNIT


def test_collapse_keeps_normal_speech():
    for t in ["да да да, это так", "Это очень очень важно.", "ha ha", "Привет. Как дела? Привет.",
              "你好，今天天氣很好。"]:
        assert collapse_repeats(t) == t and not has_loop(t)


def test_collapse_cjk_loop_and_single_word_stutter():
    assert collapse_repeats("如果您的路由器是以頻率運作，" * 5) == "如果您的路由器是以頻率運作，"
    assert collapse_repeats("нет нет нет нет нет") == "нет"
    assert collapse_repeats("Сначала " + UNIT * 0 + "а потом а потом а потом а потом конец") == "Сначала а потом конец"


def make_asr(texts, g=None):
    r = asr.FasterWhisperRecognizer.__new__(asr.FasterWhisperRecognizer)
    r.model, r.lang, r._last, r.glossary = FakeModel2(texts), "ru", "", g
    return r


class FakeModel2(FakeModel):
    def transcribe(self, audio, **kw):
        self.prompts.append(kw)
        return [FakeSeg(self.texts.pop(0))], type("I", (), {"language": "ru"})()


def test_looped_final_is_redecoded_without_prompt_and_temperature_fallback():
    r = make_asr(["Привет, это начало.", LOOP, "Если ваш роутер работает на частоте 5 ГГц, то да."])
    a = np.zeros(10, np.float32)
    r.transcribe(a)
    t = r.transcribe(a)
    first, retry = r.model.prompts[1], r.model.prompts[2]
    assert len(first["temperature"]) > 1 and first["initial_prompt"] == "Привет, это начало."  # fallback on
    assert retry["initial_prompt"] is None and min(retry["temperature"]) > 0 and retry["repetition_penalty"] > 1
    assert t.text == "Если ваш роутер работает на частоте 5 ГГц, то да."


def test_still_looped_after_retry_is_collapsed_and_not_used_as_prompt():
    r = make_asr([LOOP, LOOP])
    t = r.transcribe(np.zeros(10, np.float32))
    assert t.text == UNIT
    assert r._last == ""  # is_garbage(LOOP-collapsed) is fine, but the next prompt must not carry a loop
    assert len(r.model.prompts) == 2


def test_partial_collapses():
    r = make_asr([LOOP])
    r.partial = asr.FasterWhisperRecognizer.partial.__get__(r)
    r.model.transcribe = lambda audio, **kw: ([FakeSeg(LOOP)], type("I", (), {"language": "ru"})())
    assert r.partial(np.zeros(10, np.float32)).text == UNIT


def test_qwen_translate_collapses_looped_source(tmp_path):
    from tests.test_qwen import FakeLlm, fake_models

    m = fake_models(tmp_path, "qwen3-8b")
    llm = FakeLlm("如果您的路由器是以 5.4 GHz 頻率運作")
    t = qwen.QwenTranslator(m, "qwen3-8b", loader=lambda p, g: llm)
    t.translate(LOOP, "ru", "zh")
    assert llm.calls[0][0].count(UNIT) == 1 and "если ваш роутер" not in llm.calls[0][0]


# ---- glossary ----
def test_parse_formats(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("﻿# c\n\nчастота | чистота = 頻率\nроутер\nx = \n", encoding="utf-8")
    g = glossary.load(f)
    assert [(e.sources, e.target) for e in g.entries] == [(["частота", "чистота"], "頻率"), (["роутер"], None),
                                                          (["x"], None)]


def test_load_folder_and_missing(tmp_path):
    (tmp_path / "b.txt").write_text("b = 乙\n", encoding="utf-8")
    (tmp_path / "a.txt").write_text("a = 甲\n", encoding="utf-8")
    (tmp_path / "skip.md").write_text("c = 丙\n", encoding="utf-8")
    assert [e.main for e in glossary.load(tmp_path).entries] == ["a", "b"]
    with pytest.raises(FileNotFoundError):
        glossary.load(tmp_path / "nope")
    empty = tmp_path / "e"
    empty.mkdir()
    with pytest.raises(FileNotFoundError):
        glossary.load(empty)


@pytest.fixture
def g():
    return glossary.load(EXAMPLE)


def test_example_prompt_block_stem_match(g):
    b = g.prompt_block("Чтобы сменить частоту, откройте вкладку в телефоне и камеры")
    for want in ("частота = 頻率", "вкладка = 分頁", "телефон = 手機", "камера = 攝影機"):
        assert want in b
    assert "роутер" not in b and "диапазон" not in b  # only entries that occur
    assert g.prompt_block("Привет, как дела") == ""


def test_alias_misheard_source_matches(g):
    assert "частота = 頻率" in g.prompt_block("Проверьте чистоту сигнала")


def test_context_entries_included_in_qwen_prompt(g, tmp_path):
    from tests.test_qwen import FakeLlm, fake_models

    m = fake_models(tmp_path, "qwen3-8b")
    llm = FakeLlm("好的")
    t = qwen.QwenTranslator(m, "qwen3-8b", loader=lambda p, gpu: llm)
    t.glossary = g
    t.translate("Откройте роутер.", "ru", "zh")
    assert "роутер = 路由器" in llm.calls[0][0]
    t.translate("Он работает дальше.", "ru", "zh")  # no term in the sentence, but in the context line
    assert "роутер = 路由器" in llm.calls[1][0]
    t.history.clear()
    t.translate("Он работает дальше.", "ru", "zh")
    assert "Use these translations" not in llm.calls[2][0]
    t.glossary = None
    t.translate("Откройте роутер.", "ru", "zh")
    assert "Use these translations" not in llm.calls[3][0]


def test_hotwords_budget_and_no_numbers(g):
    h = g.hotwords()
    assert "частота" in h and "Wi-Fi" in h and "2.4" not in h
    big = glossary.Glossary([glossary.Entry([f"term{i:03d}word"]) for i in range(100)])
    assert len(big.hotwords()) <= glossary.HOTWORDS_MAX_CHARS


def test_number_normalization_only_with_glossary(g):
    assert g.normalize_numbers("диапазон 2 и 4 ГГц, а также 2,4 и 5 ГГц") == "диапазон 2.4 ГГц, а также 2.4 и 5 ГГц"
    assert g.normalize_numbers("в 12,4 и 2 и 40") == "в 12,4 и 2 и 40"
    assert glossary.Glossary([glossary.Entry(["роутер"])]).normalize_numbers("2 и 4") == "2 и 4"


def test_asr_passes_hotwords_and_normalizes(g):
    r = make_asr(["диапазон 2 и 4 ГГц"], g)
    t = r.transcribe(np.zeros(10, np.float32))
    assert t.text == "диапазон 2.4 ГГц"
    assert "частота" in r.model.prompts[0]["hotwords"]
    r2 = make_asr(["ok"], None)
    r2.transcribe(np.zeros(10, np.float32))
    assert r2.model.prompts[0]["hotwords"] is None


def test_cli_flag():
    assert cli.parse_args([]).glossary is None
    assert cli.parse_args(["--glossary", "x"]).glossary == "x"
