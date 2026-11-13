import pytest

from tinglongwu import cli
from tinglongwu.engines import choose_engine
from tinglongwu.ollama import OllamaTranslator

DEFAULT_MODEL = "some-model:latest"


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
    tr = OllamaTranslator(DEFAULT_MODEL, transport=f, zh_variant="cn")
    assert tr.translate("hello", "en", "zh").text == "你好"
    _, path, body = f.calls[-1]
    assert path == "/api/chat" and body["think"] is False and body["stream"] is False
    assert body["model"] == DEFAULT_MODEL and body["options"]["temperature"] == 0


def test_history_used_as_context_but_not_for_drafts():
    f = Fake("你好")
    tr = OllamaTranslator(DEFAULT_MODEL, transport=f)
    tr.translate("hello", "en", "zh")
    tr.draft("how are you", "en", "zh")
    assert len(tr.history) == 1
    tr.translate("bye", "en", "zh")
    assert "hello => " in f.calls[-1][2]["messages"][1]["content"]


def test_same_language_and_empty_skip_server():
    f = Fake()
    tr = OllamaTranslator(DEFAULT_MODEL, transport=f)
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
    for bad in ("http://192.168.1.5:11434", "http://", "http://10.0.0.1"):
        with pytest.raises(ValueError):
            OllamaTranslator(url=bad, transport=Fake())
    OllamaTranslator(url="localhost:11434", transport=Fake())
    OllamaTranslator(url="http://[::1]:11434", transport=Fake())


def test_engine_wiring(tmp_path):
    assert choose_engine("ollama", "cpu", tmp_path) == ("ollama", "requested")
    tr = cli.make_translator("ollama", tmp_path, "tw", "cpu", say=lambda *a, **k: None, ollama_model="m")
    assert isinstance(tr, OllamaTranslator) and tr.model == "m"


def test_no_model_given_uses_first_installed_and_never_pulls(monkeypatch):
    """#356: no built-in default model; tlwu only uses models the user already pulled."""
    monkeypatch.delenv("TLWU_OLLAMA_MODEL", raising=False)
    f = Fake("ok", models=("b-model:latest", "a-model:7b"))
    tr = OllamaTranslator(transport=f)
    assert tr.model is None
    tr.translate("hello", "en", "fr")
    assert tr.model == "a-model:7b" and f.calls[-1][2]["model"] == "a-model:7b"
    assert all(path in ("/api/tags", "/api/chat") for _, path, _ in f.calls)  # never /api/pull
    with pytest.raises(RuntimeError, match="no model installed"):
        OllamaTranslator(transport=Fake(models=())).preload()


def test_env_model(monkeypatch):
    monkeypatch.setenv("TLWU_OLLAMA_MODEL", "env-model")
    assert OllamaTranslator(transport=Fake()).model == "env-model"


def test_zh_variant_conversion_and_prompt():
    f = Fake("这个软件")
    assert OllamaTranslator("m", transport=f, zh_variant="tw").translate("this software", "en", "zh").text == "這個軟體"
    assert "Taiwan" in f.calls[-1][2]["messages"][0]["content"]
    f = Fake("這個問題")
    assert OllamaTranslator("m", transport=f, zh_variant="cn").translate("this problem", "en", "zh").text == "这个问题"
    assert "Simplified" in f.calls[-1][2]["messages"][0]["content"]
    f = Fake("Bonjour")
    assert OllamaTranslator("m", transport=f).translate("Hello", "en", "fr").text == "Bonjour"
    assert "into French" in f.calls[-1][2]["messages"][0]["content"]


@pytest.mark.parametrize("free", [None, 0, 99999])
def test_auto_never_picks_ollama_or_fm(tmp_path, free):
    """#356: both engines are opt-in; auto keeps the Hy-MT2 default on every machine."""
    for dev in ("cpu", "cuda"):
        assert choose_engine("auto", dev, tmp_path, free_mib=lambda: free)[0] in ("hymt2", "hymt2-cpu")


def test_platform_defaults_unchanged():
    """#356: the GUI default engine stays auto on every platform (the Mac default is decided separately), and the
    GUI engine list is unchanged (#308/#399); ollama / fm are reachable through `--engine`."""
    from tinglongwu.gui.core import DEFAULTS, ENGINES

    assert DEFAULTS["engine"] == "auto" and DEFAULTS["show_src"] is False
    assert "ollama" not in ENGINES and "fm" not in ENGINES
    assert cli.parse_args([]).engine == "auto"
    for e in ("ollama", "fm"):
        assert cli.parse_args(["--engine", e]).engine == e

