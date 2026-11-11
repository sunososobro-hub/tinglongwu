import pytest

from tinglongwu import cli
from tinglongwu.ollama import DEFAULT_MODEL, OllamaTranslator
from tinglongwu.qwen import choose_engine


class Fake:
    def __init__(self, reply="你好", models=(DEFAULT_MODEL,)):
        self.calls, self.reply, self.models = [], reply, models

    def __call__(self, method, path, body):
        self.calls.append((method, path, body))
        if path == "/api/tags":
            return {"models": [{"name": m} for m in self.models]}
        return {"message": {"content": self.reply}}


def test_translate_request_shape_and_think_off():
    f = Fake("<think>x</think>你好")
    tr = OllamaTranslator(transport=f, zh_variant="cn")
    assert tr.translate("hello", "en", "zh").text == "你好"
    _, path, body = f.calls[-1]
    assert path == "/api/chat" and body["think"] is False and body["stream"] is False
    assert body["model"] == DEFAULT_MODEL and body["options"]["temperature"] == 0


def test_history_used_as_context_but_not_for_drafts():
    f = Fake("你好")
    tr = OllamaTranslator(transport=f)
    tr.translate("hello", "en", "zh")
    tr.draft("how are you", "en", "zh")
    assert len(tr.history) == 1
    tr.translate("bye", "en", "zh")
    assert "hello => " in f.calls[-1][2]["messages"][1]["content"]


def test_same_language_and_empty_skip_server():
    f = Fake()
    tr = OllamaTranslator(transport=f)
    assert tr.translate("hi", "en", "en").text == "hi"
    assert tr.translate("  ", "en", "zh").text == "  "
    assert f.calls == []


def test_preload_missing_model_message():
    with pytest.raises(RuntimeError, match="ollama pull"):
        OllamaTranslator("nope", transport=Fake(models=("other:latest",))).preload()
    OllamaTranslator("other", transport=Fake(models=("other:latest",))).preload()


def test_rejects_non_local_url():
    with pytest.raises(ValueError):
        OllamaTranslator(url="http://example.com:11434", transport=Fake())
    OllamaTranslator(url="localhost:11434", transport=Fake())


def test_engine_wiring(tmp_path):
    assert choose_engine("ollama", "cpu", tmp_path) == ("ollama", "requested")
    tr = cli.make_translator("ollama", tmp_path, "tw", "cpu", say=lambda *a, **k: None, ollama_model="m")
    assert isinstance(tr, OllamaTranslator) and tr.model == "m"
