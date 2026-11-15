"""#942: Hindi goes to Hy-MT2 direct (hi -> zh and back), with the language named in the prompt; no ModelNotFound."""
from tinglongwu.hymt import HyMtTranslator, JaRouter, build_prompt, hymt_path


class FakeLlm:
    def __init__(self):
        self.prompts = []

    def create_chat_completion(self, messages, **kw):
        self.prompts.append(messages[0]["content"])
        return {"choices": [{"message": {"content": "大家好"}}]}


def _listen_router(tmp_path):
    """The listen path since #471: base=None, Hy-MT2 is the only engine."""
    p = hymt_path(tmp_path)
    p.parent.mkdir(parents=True)
    p.write_bytes(b"x")
    llm = FakeLlm()
    tr = JaRouter(None, tmp_path, "tw", "cpu", all_pairs=True,
                  factory=lambda *a: HyMtTranslator(tmp_path, "tw", "cpu", loader=lambda path, gpu: llm))
    return tr, llm


def test_hindi_to_chinese_calls_hymt(tmp_path):
    tr, llm = _listen_router(tmp_path)
    out = tr.translate("नमस्ते, आप कैसे हैं?", "hi", "zh")
    assert out.text and len(llm.prompts) == 1
    assert "नमस्ते" in llm.prompts[0] and "翻译成中文" in llm.prompts[0]


def test_chinese_and_english_to_hindi_name_hindi_in_the_prompt(tmp_path):
    tr, llm = _listen_router(tmp_path)
    tr.translate("大家好", "zh", "hi")
    tr.translate("Hello everyone", "en", "hi")
    assert "翻译成印地语" in llm.prompts[0] and "into Hindi." in llm.prompts[1]
    assert build_prompt("Hello everyone", "en", "hi") in llm.prompts[1]
