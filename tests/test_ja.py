"""Japanese: Hy-MT2 routing, <unk> stripping, Kokoro ja voices, --to ja / follow wiring. No real models."""
import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from tinglongwu import hymt
from tinglongwu.hymt import HyMtTranslator, JaRouter, build_prompt, hymt_path
from tinglongwu.reverse import proto, tts_client
from tinglongwu.reverse.logic import split_sentences, translate_ru
from pivot_fake import PivotTranslator
from tinglongwu.translate import strip_unk

from test_reverse_en import _parse, _worker_const

WORKER = Path(tts_client.__file__).with_name("tts_worker.py")


class FakeLlm:
    def __init__(self, reply="明日の夜7時に予約したいです。"):
        self.reply, self.calls = reply, []

    def create_chat_completion(self, messages, **kw):
        self.calls.append((messages, kw))
        return {"choices": [{"message": {"content": self.reply}}]}


def install_hy(tmp_path):
    p = hymt_path(tmp_path)
    p.parent.mkdir(parents=True)
    p.write_bytes(b"x")
    return tmp_path


def hy_factory(m, llm=None, loader=None):
    return lambda *a: HyMtTranslator(m, "tw", "cpu", loader=loader or (lambda p, g: llm))


def test_strip_unk_field_samples():
    assert strip_unk("I want to do my best 4th<unk> in the back") == "I want to do my best 4th in the back"
    assert strip_unk("<unk>WBC is more than 3 years ago") == "WBC is more than 3 years ago"
    assert strip_unk('我想在後面做我最好的第4個"unk"') == "我想在後面做我最好的第4個"
    assert strip_unk("你好<unk>。") == "你好。"
    assert strip_unk("plain text, unk is fine") == "plain text, unk is fine"


def test_prompt_templates():
    assert build_prompt("你好", "zh", "ja") == "将以下文本翻译成日语，注意只需要输出翻译后的结果，不要额外解释：\n\n你好"
    assert "into Japanese" in build_prompt("hello", "en", "ja")


def test_hymt_translates_and_converts_zh(tmp_path):
    m = install_hy(tmp_path)
    llm = FakeLlm("明天晚上七点，我们想订位。")
    tr = HyMtTranslator(m, "tw", "cpu", loader=lambda path, gpu: llm)
    out = tr.translate("明日の夜7時に予約したいです。", "ja", "zh")
    assert out.pivot is None and out.text == "明天晚上七點，我們想訂位。"
    msgs, kw = llm.calls[0]
    assert msgs[0]["content"].startswith("将以下文本翻译成中文") and kw["top_k"] == 20
    assert tr.translate("あ", "ja", "ja").text == "あ" and len(llm.calls) == 1


def test_hymt_missing_model_hint(tmp_path):
    from tinglongwu.models import ModelNotFound

    with pytest.raises(ModelNotFound, match="--hymt"):
        HyMtTranslator(tmp_path)


def pivot(log):
    def factory(s, t):
        return lambda x: (log.append((s, t)) or f"{s}>{t}:{x}")
    return PivotTranslator(None, "tw", step_factory=factory)


def test_router_ja_direct_when_installed_no_pivot(tmp_path):
    m, log = install_hy(tmp_path), []
    r = JaRouter(pivot(log), m, "tw", "cpu", factory=hy_factory(m, FakeLlm("我想努力")))
    t = r.translate("頑張りたい", "ja", "zh")
    assert t.pivot is None and t.text == "我想努力" and log == []  # no [EN] line, OPUS untouched
    assert r.translate("hi", "en", "zh").pivot is None and log == [("en", "zh")]  # other pairs: base


def test_router_falls_back_to_opus_pivot_without_model(tmp_path):
    log, warns = [], []
    r = JaRouter(pivot(log), tmp_path, "tw", "cpu", warn=warns.append)
    t = r.translate("頑張りたい", "ja", "zh")
    assert t.pivot == "ja>en:頑張りたい" and log == [("ja", "en"), ("en", "zh")]
    r.preload("ja", "zh")
    assert warns and "OPUS pivot" in warns[0]
    with pytest.raises(RuntimeError, match="Hy-MT2"):
        r.translate("你好", "zh", "ja")
    with pytest.raises(RuntimeError):
        r.preload("zh", "ja")


def test_router_falls_back_when_llm_fails_to_load(tmp_path):
    m, log, warns = install_hy(tmp_path), [], []

    def bad(path, gpu):
        raise RuntimeError("no llama")

    r = JaRouter(pivot(log), m, "tw", "cpu", factory=hy_factory(m, loader=bad), warn=warns.append)
    assert r.translate("頑張りたい", "ja", "zh").pivot == "ja>en:頑張りたい"
    assert any("falls back" in w for w in warns)
    r2 = JaRouter(pivot([]), m, "tw", "cpu", factory=hy_factory(m, loader=bad))
    with pytest.raises(RuntimeError):  # zh -> ja has no fallback
        r2.translate("你好", "zh", "ja")


def test_make_translator_no_longer_builds_opus(tmp_path):
    from tinglongwu import cli

    with pytest.raises(SystemExit, match="--engine auto"):  # #471: no OPUS pivot in the listen path
        cli.make_translator("opus", tmp_path, "tw", "cpu", say=lambda *a, **k: None,
                            choose=lambda e, d, m: ("opus", "test"))


def test_translate_ru_no_cjk_retry_for_japanese():
    calls = []
    out, retries = translate_ru(lambda x: calls.append(x) or "予約したいです。", "我想预订", "ja")
    assert (out, retries) == ("予約したいです。", 0) and len(calls) == 1


def test_split_sentences_japanese():
    assert split_sentences("今日は晴れです。明日は雨です。") == ["今日は晴れです。", "明日は雨です。"]
    long = "あ" * 100 + "、" + "い" * 150
    parts = split_sentences(long)
    assert all(len(p) <= 220 for p in parts) and "".join(parts) == "あ" * 100 + "い" * 150


def test_langfollow_defaults_include_ja(tmp_path):
    from tinglongwu.langfollow import DEFAULT_CANDIDATES, read_lang, write_lang

    assert DEFAULT_CANDIDATES == ("en", "ru", "ja", "zh")
    write_lang(tmp_path / "f", "ja")
    assert read_lang(tmp_path / "f") == "ja"


def test_ja_voice_map():
    ns = {"sys": __import__("sys")}
    tree = ast.parse(WORKER.read_text("utf-8"))
    keep = [n for n in tree.body if (isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") in
                                     ("KOKORO_VOICES", "KOKORO_JA_VOICES"))
            or (isinstance(n, ast.FunctionDef) and n.name == "kokoro_voice")]
    exec(compile(ast.Module(body=keep, type_ignores=[]), "w", "exec"), ns)  # noqa: S102
    kv, ja = ns["kokoro_voice"], _worker_const("KOKORO_JA_VOICES")
    assert len(ja) == 5 and all(v.startswith(("jf_", "jm_")) for v in ja)
    assert kv(0, voices=ja) == "jf_alpha" and kv(4, voices=ja) == "jm_kumo"
    assert kv(9, voices=ja, warn=lambda m: None) == "jf_alpha"
    assert kv(0) == "am_michael"


def test_client_sends_lang_for_kokoro_only():
    sent = []

    class P:
        def __init__(self):
            self.stdin = SimpleNamespace(write=lambda b: sent.append(b), flush=lambda: None)
            self.stdout = SimpleNamespace(readline=lambda: b'{"t":"done"}\n', read=lambda n: b"")

    c = tts_client.TTSClient.__new__(tts_client.TTSClient)
    c.engine, c.speaker, c.lang, c._n, c.proc = "kokoro", 0, "ja", 0, P()
    list(c.speak("こんにちは"))
    list(c.speak("hello", lang="en"))
    assert b'"lang": "ja"' in sent[0] and b'"lang": "en"' in sent[1]
    c.engine = "vosk"
    list(c.speak("привет"))
    assert b"lang" not in sent[2]


def test_follow_tts_routes_japanese_to_kokoro_client():
    made = []

    class C:
        def __init__(self, k):
            self.k, self.speaker = k, 0
            made.append(k)

        def speak(self, t, lang=None):
            return (self.k, lang)

    tts = proto.FollowTTS({"en": lambda: C("en"), "ru": lambda: C("ru")})
    assert tts.speak("今日は晴れです。") == ("en", "ja") and made == ["en"]  # same worker, no extra process
    assert tts.speak("会議") == ("en", "ja")
    assert tts.speak("hello") == ("en", "en") and tts.speak("привет") == ("ru", None)


def test_cli_to_ja_and_follow_langs():
    a = _parse(["--to", "ja", "--tts-engine", "kokoro", "--mt", "hymt"])
    assert (a.to, a.speaker) == ("ja", 0)
    with pytest.raises(SystemExit):
        _parse(["--to", "ja", "--tts-engine", "vosk"])
    with pytest.raises(SystemExit):
        _parse(["--to", "ja"])  # default vosk TTS is Russian-only
    assert _parse(["--to", "ja", "--no-tts"]).to == "ja"
    a = _parse(["--to", "follow", "--tts-engine", "kokoro", "--tts-model", "k", "--follow-langs", "en,ja"])
    assert a.follow_set == ("en", "ja")  # no Vosk needed without ru
    a = _parse(["--to", "follow", "--tts-engine", "kokoro", "--tts-model", "k"])
    assert a.ru_voice is False  # Russian voice model is opt-in: missing -> subtitles only, no error
    with pytest.raises(SystemExit):
        _parse(["--to", "follow", "--no-tts", "--follow-langs", "en,de"])


def test_setup_has_hymt_pin():
    import json

    from tinglongwu_setup import downloader as dl

    assert dl.HYMT_GGUF[0] == hymt.HYMT_REPO and dl.HYMT_GGUF[1] == hymt.HYMT_FILE
    pins = json.loads(dl.PINS_FILE.read_text("utf-8"))["models"]
    assert pins[hymt.HYMT_DIR]["files"][hymt.HYMT_FILE]
