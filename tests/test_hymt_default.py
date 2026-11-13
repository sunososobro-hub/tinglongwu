"""#66: Hy-MT2 is the default GPU translation engine (auto on CUDA), Qwen stays optional. No real models."""
import pytest

from tinglongwu import cli
from tinglongwu.hymt import HyMtTranslator, JaRouter, hymt_path, zh_script_guard
from tinglongwu.engines import HYMT_MIN_FREE_MIB, choose_engine


class FakeLlm:
    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def create_chat_completion(self, messages, **kw):
        self.calls.append(messages[0]["content"])
        return {"choices": [{"message": {"content": self.reply}}]}


class Base:
    """Stands in for the OPUS pivot."""

    def __init__(self):
        self.calls = []

    def preload(self, src, tgt):
        pass

    def translate(self, text, src, tgt):
        from tinglongwu.translate import Translation

        self.calls.append((text, src, tgt))
        return Translation(None, "opus:" + text)


def install(tmp_path, *names):
    for n in names:
        p = hymt_path(tmp_path) if n == "hymt2" else tmp_path / n / f"{n}.gguf"  # an old Qwen GGUF left on disk
        p.parent.mkdir(parents=True)
        p.write_bytes(b"x")
    return tmp_path


def test_auto_is_hymt_on_every_device(tmp_path):
    """#276: one engine. Enough VRAM -> Hy-MT2 on the GPU; less, unknown, or CPU -> Hy-MT2 on the CPU; never Qwen/OPUS."""
    m = install(tmp_path, "hymt2", "qwen3-8b", "qwen3-4b")
    assert choose_engine("auto", "cuda", m, free_mib=lambda: 8000)[0] == "hymt2"  # 8B would fit, still Hy-MT2
    assert choose_engine("auto", "cuda", m, free_mib=lambda: HYMT_MIN_FREE_MIB)[0] == "hymt2"
    assert choose_engine("auto", "cuda", m, free_mib=lambda: HYMT_MIN_FREE_MIB - 1)[0] == "hymt2-cpu"
    assert choose_engine("auto", "cuda", m, free_mib=lambda: None)[0] == "hymt2-cpu"
    assert choose_engine("auto", "cpu", m, free_mib=lambda: 99999)[0] == "hymt2-cpu"


def test_auto_without_hymt_does_not_pick_qwen(tmp_path):
    m = install(tmp_path, "qwen3-8b")
    assert choose_engine("auto", "cuda", m, free_mib=lambda: 8000)[0] == "hymt2"


def test_explicit_engines(tmp_path):
    m = install(tmp_path, "hymt2")
    assert cli.parse_args(["--engine", "hymt"]).engine == "hymt"
    assert choose_engine("hymt", "cpu", m)[0] == "hymt2"


@pytest.mark.parametrize("engine,dev", [("hymt", "cuda"), ("auto", "cpu"), ("auto", "cuda")])
def test_missing_hymt_is_a_setup_error_not_opus(tmp_path, engine, dev):
    said = []
    with pytest.raises(SystemExit, match="tlwu-setup --hymt"):
        cli.make_translator(engine, tmp_path, "tw", dev, say=lambda m, **k: said.append(m),
                            choose=lambda e, d, md: choose_engine(e, d, md, free_mib=lambda: 6000))
    assert not any("engine = opus" in m for m in said)


def test_make_translator_hymt_translates_all_known_pairs_directly(tmp_path, monkeypatch):
    m = install(tmp_path, "hymt2")
    llm = FakeLlm("明天見。")
    monkeypatch.setattr("tinglongwu.hymt._load_llama", lambda path, gpu: llm)
    said = []
    tr = cli.make_translator("auto", m, "tw", "cuda", say=lambda msg, **k: said.append(msg),
                             choose=lambda e, d, md: choose_engine(e, d, md, free_mib=lambda: 6000))
    assert any("engine = hymt2" in s for s in said)
    assert tr.translate("Увидимся завтра, хорошо?", "ru", "zh").text == "明天見。"
    assert tr.translate("See you tomorrow, all right?", "en", "zh").text == "明天見。"
    assert len(llm.calls) == 2 and "Увидимся" in llm.calls[0]  # direct, no OPUS / English pivot


def test_make_translator_hymt_load_failure_stops_not_opus(tmp_path, monkeypatch):
    m = install(tmp_path, "hymt2")

    def boom(path, gpu):
        raise RuntimeError("CUDA out of memory")

    monkeypatch.setattr("tinglongwu.hymt._load_llama", boom)
    said = []
    with pytest.raises(SystemExit, match="CUDA out of memory") as e:
        cli.make_translator("hymt", m, "tw", "cuda", say=lambda msg, **k: said.append(msg))
    assert "tlwu-setup --hymt" in str(e.value)
    assert not any("engine = opus" in s for s in said)


def test_low_vram_runs_hymt_on_the_cpu(tmp_path, monkeypatch):
    m = install(tmp_path, "hymt2")
    gpus = []
    monkeypatch.setattr("tinglongwu.hymt._load_llama", lambda path, gpu: gpus.append(gpu) or FakeLlm("你好"))
    said = []
    tr = cli.make_translator("auto", m, "tw", "cuda", say=lambda msg, **k: said.append(msg),
                             choose=lambda e, d, md: choose_engine(e, d, md, free_mib=lambda: HYMT_MIN_FREE_MIB - 1))
    assert gpus == [False] and any("engine = hymt2 on cpu" in s for s in said)
    assert tr.translate("Hello", "en", "zh").text == "你好"


def test_router_all_pairs_sends_unknown_language_to_base(tmp_path):
    m = install(tmp_path, "hymt2")
    llm, base = FakeLlm("你好"), Base()
    tr = JaRouter(base, m, "tw", "cpu", all_pairs=True,
                  factory=lambda *a: HyMtTranslator(m, "tw", "cpu", loader=lambda p, g: llm))
    assert tr.translate("Hello there", "en", "zh").text == "你好" and not base.calls
    assert tr.translate("Merhaba", "tr", "zh").text == "opus:Merhaba"  # Turkish is not in Hy-MT2's table here
    # default router (other engines): only Japanese pairs use Hy-MT2
    ja_only = JaRouter(base, m, "tw", "cpu", factory=lambda *a: HyMtTranslator(m, "tw", "cpu", loader=lambda p, g: llm))
    assert ja_only.translate("Hello there", "en", "zh").text == "opus:Hello there"


def test_listen_router_has_no_opus_fallback(tmp_path, monkeypatch):
    """#471: the listen path builds Hy-MT2 with no OPUS pivot behind it: an unknown language, or a Hy-MT2 that fails
    mid-session, is an error naming `tlwu-setup --hymt`, never a silent switch to OPUS."""
    from tinglongwu.translate import PivotTranslator

    m = install(tmp_path, "hymt2")
    monkeypatch.setattr(PivotTranslator, "__init__", lambda *a, **k: pytest.fail("OPUS pivot built"))
    llm = FakeLlm("你好")
    monkeypatch.setattr("tinglongwu.hymt._load_llama", lambda path, gpu: llm)
    warned = []
    tr = cli.make_translator("hymt", m, "tw", "cpu", say=lambda msg, **k: warned.append(msg))
    assert tr.translate("Hello", "en", "zh").text == "你好"
    from tinglongwu.models import ModelNotFound

    with pytest.raises(ModelNotFound, match="does not translate tr -> zh"):
        tr.preload("tr", "zh")  # Turkish is not in Hy-MT2's table
    with pytest.raises(ModelNotFound):  # e.g. auto-detected by Whisper: the pipeline reports it and keeps running
        tr.translate("Merhaba", "tr", "zh")
    llm.create_chat_completion = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("llama crashed"))
    with pytest.raises(RuntimeError, match="not available.*llama crashed"):
        tr.translate("Hello", "en", "zh")
    assert any("no fallback engine" in w for w in warned) and not any("OPUS" in w for w in warned)


def test_short_line_left_in_kana_shows_source(tmp_path):
    m = install(tmp_path, "hymt2")
    tr = HyMtTranslator(m, "tw", "cpu", loader=lambda p, g: FakeLlm("ありがとうございます"))
    assert tr.translate("ありがとうございます", "ja", "zh").text == "ありがとうございます"
    assert zh_script_guard("Спасибо", "Спасибо。") == "Спасибо"
    assert zh_script_guard("Hey", "欸，齁") == "欸，齁"  # valid Taiwan interjections are kept
    long_src = "это довольно длинное предложение, которое модель перевела почти полностью"
    assert zh_script_guard(long_src, "這是一個相當長的句子 модель") == "這是一個相當長的句子 модель"


def test_auto_detected_unsupported_language_keeps_the_session_running(tmp_path, wav, monkeypatch, capsys):
    """#471 review: with no OPUS pivot, a language Whisper detects but Hy-MT2 does not know (Turkish here) is reported
    as a missing pair and the session goes on; it must not end the live session."""
    import io

    from tinglongwu.asr import Recognizer, Transcript
    from tinglongwu.capture import FileSource
    from tinglongwu.pipeline import run
    from tinglongwu.segment import Segmenter
    from tinglongwu.sink import TextSink

    class TurkishASR(Recognizer):
        n = 0

        def transcribe(self, audio):
            self.n += 1
            return Transcript(f"merhaba dünya {self.n}", "tr")

    m = install(tmp_path, "hymt2")
    monkeypatch.setattr("tinglongwu.hymt._load_llama", lambda path, gpu: FakeLlm("你好"))
    tr = cli.make_translator("hymt", m, "tw", "cpu", say=lambda msg, **k: None)
    out = io.StringIO()
    run(FileSource(str(wav)), Segmenter(chunk=1.0, use_vad=False), TurkishASR(), tr, TextSink(out))
    assert out.getvalue().count("merhaba dünya") >= 2  # every segment was shown, the run did not stop
    assert "tr -> zh" in out.getvalue() + capsys.readouterr().err
