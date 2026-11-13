"""#335: it/pt/vi/th/id go to Hy-MT2 directly (not the OPUS pivot), with full language names in the prompt."""
import pytest

from tinglongwu.hymt import HyMtTranslator, JaRouter, build_prompt, hymt_path
from tinglongwu.translate import Translation

NEW = {"it": ("Italian", "意大利语"), "pt": ("Portuguese", "葡萄牙语"), "vi": ("Vietnamese", "越南语"),
       "th": ("Thai", "泰语"), "id": ("Indonesian", "印尼语")}


class FakeLlm:
    def __init__(self):
        self.prompts = []

    def create_chat_completion(self, messages, **kw):
        self.prompts.append(messages[0]["content"])
        return {"choices": [{"message": {"content": "hy-out"}}]}


class Pivot:
    def __init__(self):
        self.calls = []

    def preload(self, src, tgt):
        pass

    def translate(self, text, src, tgt):
        self.calls.append((src, tgt))
        return Translation(None, "opus:" + text)


def _router(tmp_path):
    p = hymt_path(tmp_path)
    p.parent.mkdir(parents=True)
    p.write_bytes(b"x")
    llm, pivot = FakeLlm(), Pivot()
    tr = JaRouter(pivot, tmp_path, "tw", "cpu", all_pairs=True,
                  factory=lambda *a: HyMtTranslator(tmp_path, "tw", "cpu", loader=lambda path, gpu: llm))
    return tr, llm, pivot


@pytest.mark.parametrize("lang", sorted(NEW))
def test_new_language_routes_to_hymt_both_directions(tmp_path, lang):
    tr, llm, pivot = _router(tmp_path)
    assert tr.translate("Ciao a tutti, come va?", lang, "zh").text != "opus:Ciao a tutti, come va?"
    tr.translate("大家好，今天天氣很好。", "zh", lang)
    tr.translate("Hello everyone, how are you?", "en", lang)
    assert pivot.calls == [] and len(llm.prompts) == 3


@pytest.mark.parametrize("lang", sorted(NEW))
def test_prompt_uses_full_language_name(lang):
    en, zh = NEW[lang]
    zh_prompt = build_prompt("你好", "zh", lang)
    en_prompt = build_prompt("Hello", "en", lang)
    assert zh in zh_prompt and en in en_prompt
    assert f"翻译成{lang}" not in zh_prompt and f"into {lang}." not in en_prompt


def test_unlisted_language_still_goes_to_pivot(tmp_path):
    tr, llm, pivot = _router(tmp_path)
    tr.translate("Merhaba", "tr", "zh")
    assert pivot.calls == [("tr", "zh")] and llm.prompts == []
