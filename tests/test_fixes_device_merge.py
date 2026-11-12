import io
import threading
import time
import urllib.request

import numpy as np
import pytest

from tinglongwu import cli, device
from tinglongwu.asr import Recognizer, Transcript
from tinglongwu.merge import SentenceMerger
from tinglongwu.pipeline import run
from tinglongwu.segment import Segmenter
from tinglongwu.sink import TextSink
from tinglongwu.translate import OpusStep, PivotTranslator


# ---- OpusStep._detok ----
def _step(join):
    s = OpusStep.__new__(OpusStep)
    s.join = join
    return s


def test_detok_cjk_markers_spaces_punct():
    s = _step("")
    assert s._detok(["▁在", "加里", "寧格勒", "▁,", "他", "們", "▁好", "▁."]) == "在加里寧格勒，他們好。"
    assert s._detok(["▁你", "▁好"]) == "你好"


def test_detok_keeps_latin_inside_cjk():
    assert _step("")._detok(["▁用", "▁PDF", "▁文件", "▁!"]) == "用 PDF 文件！"


def test_detok_latin_target_keeps_spaces_and_ascii_punct():
    assert _step(" ")._detok(["▁Hello", "▁world", "▁,", "▁ok"]) == "Hello world , ok"


# ---- downloader._open User-Agent ----
def test_open_sets_user_agent(monkeypatch):
    from tinglongwu_setup import downloader

    seen = {}

    def fake(req, timeout=None):
        seen["ua"] = req.get_header("User-agent")
        seen["url"], seen["timeout"] = req.full_url, timeout
        return "resp"

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    assert downloader._open("https://example.invalid/x", 7) == "resp"
    assert seen == {"ua": "tlwu-setup/0.1", "url": "https://example.invalid/x", "timeout": 7}


def test_whisper_repo_mapping():
    from tinglongwu_setup.downloader import whisper_repo

    assert whisper_repo("small") == "Systran/faster-whisper-small"
    assert whisper_repo("large-v3-turbo") == "mobiuslabsgmbh/faster-whisper-large-v3-turbo"


# ---- device selection ----
def test_resolve_cpu_and_auto_paths():
    msgs = []

    def rd(c, count, missing):
        return device.resolve_device(c, count=count, missing=missing, warn=msgs.append, driver=lambda: None)

    assert rd("cpu", lambda: 1, lambda: []) == "cpu"
    assert rd("auto", lambda: 0, lambda: []) == "cpu" and not msgs  # silent for auto
    assert rd("auto", lambda: 1, lambda: []) == "cuda"
    assert rd("cuda", lambda: 1, lambda: []) == "cuda"
    assert rd("cuda", lambda: 0, lambda: []) == "cpu" and "no CUDA device" in msgs[-1]


def test_resolve_missing_libs_falls_back_with_message():
    msgs = []
    r = device.resolve_device("auto", count=lambda: 1, missing=lambda: ["cublas64_12.dll"], warn=msgs.append)
    assert r == "cpu" and "cublas64_12.dll" in msgs[0] and "tlwu-setup --gpu" in msgs[0]


def test_resolve_rejects_bad_choice():
    with pytest.raises(ValueError):
        device.resolve_device("tpu")


def test_compute_types():
    assert device.compute_type("cpu", "asr") == device.compute_type("cpu", "mt") == "int8"
    assert device.compute_type("cuda", "asr") == "float16"
    assert device.compute_type("cuda", "mt") == "int8_float16"


def test_cli_device_and_merge_args():
    a = cli.parse_args([])
    assert a.compute == "auto" and a.max_wait == 6.0 and a.chunk == 8.0 and not a.no_merge
    assert cli.parse_args(["--device", "cuda"]).compute == "cuda"
    assert cli.parse_args(["--capture-device", "Speakers"]).device == "Speakers"
    assert cli.parse_args(["--model", "large-v3-turbo"]).model == "large-v3-turbo"
    with pytest.raises(SystemExit):
        cli.parse_args(["--device", "tpu"])


# ---- SentenceMerger ----
def texts(units):
    return [u.text for u in units]


def test_merge_joins_midsentence_and_releases_at_period():
    m = SentenceMerger()
    assert m.add("самое крутое место", "ru", 0) == []
    assert texts(m.add("это Мельбурн. А потом", "ru", 1)) == ["самое крутое место это Мельбурн."]
    assert m.buf == "А потом"
    assert texts(m.flush()) == ["А потом"]


def test_merge_multiple_sentences_in_one_segment():
    m = SentenceMerger()
    assert texts(m.add("Раз. Два! Три?", "ru", 0)) == ["Раз.", "Два!", "Три?"]
    assert m.deadline is None


def test_merge_abbreviation_not_boundary():
    m = SentenceMerger()
    assert m.add("в Иркутске и т. д.", "ru", 0) == []
    assert texts(m.add("и так далее. Конец.", "ru", 1)) == ["в Иркутске и т. д. и так далее.", "Конец."]


def test_merge_cjk_boundaries():
    m = SentenceMerger()
    assert texts(m.add("你好。我是", "zh", 0)) == ["你好。"]
    assert texts(m.add("学生。", "zh", 1)) == ["我是学生。"]


def test_merge_timeout_and_cap():
    m = SentenceMerger(max_wait=6, max_words=5)
    m.add("без точки", "ru", 10)
    assert m.deadline == 16
    assert m.due(15.9) == []
    u = m.due(16.0)
    assert texts(u) == ["без точки"] and u[0].forced and m.deadline is None
    m2 = SentenceMerger(max_wait=60, max_words=5)
    assert texts(m2.add("один два три четыре пять", "ru", 0)) == ["один два три четыре пять"]


def test_merge_add_after_deadline_flushes():
    m = SentenceMerger(max_wait=6)
    m.add("начало фразы", "ru", 0)
    assert texts(m.add("продолжение", "ru", 7)) == ["начало фразы продолжение"]


def test_merge_dedupes_vad_overlap():
    m = SentenceMerger()
    m.add("была серая зима", "ru", 0)
    assert texts(m.add("серая зима и снег.", "ru", 1)) == ["была серая зима и снег."]
    m2 = SentenceMerger()
    m2.add("Всё серая зима.", "ru", 0)  # overlap across an already-emitted sentence boundary
    assert texts(m2.add("серая зима и снег.", "ru", 1)) == ["и снег."]
    m3 = SentenceMerger()  # one-word repeat is kept (min_overlap=2)
    m3.add("очень", "ru", 0)
    assert texts(m3.add("очень хорошо.", "ru", 1)) == ["очень очень хорошо."]


def test_merge_language_change_flushes():
    m = SentenceMerger()
    m.add("hello there", "en", 0)
    assert texts(m.add("привет.", "ru", 1)) == ["hello there", "привет."]


# ---- pipeline integration ----
class ScriptASR(Recognizer):
    def __init__(self, items):
        self.items = list(items)

    def transcribe(self, audio):
        return Transcript(self.items.pop(0), "ru")


class Src:
    def __init__(self, n, hold=None):
        self.n, self.hold = n, hold

    def frames(self):
        for _ in range(self.n):
            yield np.ones(16000, np.float32) * 0.3
        if self.hold:
            self.hold.wait(5)

    def close(self):
        pass


def _tr(tmp_path):
    return PivotTranslator(tmp_path, "tw", lambda s, t: (lambda x: x.upper()) if (s, t) == ("ru", "en")
                           else (lambda x: x))


def test_pipeline_merges_segments(tmp_path):
    out = io.StringIO()
    run(Src(3), Segmenter(chunk=1.0, use_vad=False), ScriptASR(["половина", "фразы.", "другая."]),
        _tr(tmp_path), TextSink(out), merger=SentenceMerger(), audio_clock=True)
    o = out.getvalue()
    assert "Them половина фразы." in o and "Them другая." in o and o.count("Them") == 2


def test_pipeline_timer_flushes_without_new_segment(tmp_path):
    """Live mode: a held partial sentence is translated after max_wait even if the stream stalls."""
    hold = threading.Event()
    out = io.StringIO()
    t0 = time.monotonic()
    seen = {}

    class Sink(TextSink):
        def write(self, r):
            seen["t"] = time.monotonic() - t0
            super().write(r)
            hold.set()

    run(Src(1, hold=hold), Segmenter(chunk=1.0, use_vad=False), ScriptASR(["без точки"]),
        _tr(tmp_path), Sink(out), merger=SentenceMerger(max_wait=0.4))
    assert "Them без точки" in out.getvalue()
    assert 0.3 <= seen["t"] < 2.5


def test_pipeline_propagates_producer_error(tmp_path):
    class Bad(Src):
        def frames(self):
            raise RuntimeError("capture broke")
            yield

    with pytest.raises(RuntimeError, match="capture broke"):
        run(Bad(1), Segmenter(chunk=1.0, use_vad=False), ScriptASR([]), _tr(tmp_path), TextSink(io.StringIO()))


# ---- presets, forced-cut ellipsis, adaptive pause, join dedupe, zh_convert ----
def test_preset_defaults_and_override():
    a = cli.parse_args([])
    assert (a.chunk, a.max_wait) == (8.0, 6.0)
    a = cli.parse_args(["--preset", "chat"])
    assert (a.chunk, a.max_wait) == (4.0, 1.5)
    a = cli.parse_args(["--preset", "chat", "--chunk", "6", "--max-wait", "3"])
    assert (a.chunk, a.max_wait) == (6.0, 3.0)
    assert cli.parse_args(["--preset", "meeting", "--max-wait", "2"]).chunk == 8.0


def test_gui_settings_pass_preset():
    from tinglongwu.gui.core import settings_to_argv

    assert cli.parse_args(settings_to_argv({"preset": "chat"})).chunk == 4.0
    assert cli.parse_args(settings_to_argv({})).chunk == 8.0


def test_forced_cut_ellipsis_is_not_sentence_end():
    m = SentenceMerger(max_wait=6)
    assert m.add("пусть едут через Кита...", "ru", 0, forced_cut=True) == []
    assert texts(m.add("Через Китай в Австралию.", "ru", 1)) == ["пусть едут через Кита Через Китай в Австралию."]
    m2 = SentenceMerger()  # a real VAD pause keeps "..." as a sentence end
    assert texts(m2.add("Ну не знаю…", "ru", 0, forced_cut=False)) == ["Ну не знаю…"]
    m3 = SentenceMerger()  # but a real period on a forced cut still ends the sentence
    assert texts(m3.add("Всё готово.", "ru", 0, forced_cut=True)) == ["Всё готово."]


def test_pause_releases_unfinished_buffer_but_forced_cut_does_not():
    m = SentenceMerger(max_wait=6)
    u = m.add("я был в Австралии", "ru", 0, forced_cut=False, pause=0.9)
    assert texts(u) == ["я был в Австралии"] and m.deadline is None
    m2 = SentenceMerger(max_wait=6)
    assert m2.add("я был в", "ru", 0, forced_cut=False, pause=0.5) == []
    m3 = SentenceMerger(max_wait=6)
    assert m3.add("я был в", "ru", 0, forced_cut=True, pause=2.0) == []  # forced cut: speech continues
    assert m3.deadline == 6


def test_join_dedupes_single_word_after_forced_cut():
    m = SentenceMerger()
    m.add("нужна австралийскую...", "ru", 0, forced_cut=True)
    assert texts(m.add("австралийскую визу.", "ru", 1)) == ["нужна австралийскую визу."]
    m2 = SentenceMerger()  # without a cut a single repeated word is kept
    m2.add("очень", "ru", 0)
    assert texts(m2.add("очень хорошо.", "ru", 1)) == ["очень очень хорошо."]


def test_zh_convert_keeps_tongguo_zhichi_but_taiwan_terms():
    from tinglongwu.translate import zh_convert

    assert zh_convert("通过签证支持", "tw") == "通過簽證支持"
    assert zh_convert("通過這個，不支持", "tw") == "通過這個，不支持"
    assert zh_convert("软件和网络", "tw") == "軟體和網路"
    assert zh_convert("通过支持", "cn") == "通过支持"


def test_zh_convert_289_no_phrase_corruption():
    from tinglongwu.translate import zh_convert

    assert zh_convert("音乐家具有更好的记忆", "tw") == "音樂家具有更好的記憶"
    assert zh_convert("连接两个脑半球的桥梁", "tw") == "連接兩個腦半球的橋樑"
    assert zh_convert("大脑连接", "tw") == "大腦連接"
    assert zh_convert("软件和内存", "tw") == "軟體和記憶體"


def test_segmenter_reports_forced_cut_and_pause():
    sr = 16000
    frames = lambda n: (np.ones(sr // 2, np.float32) for _ in range(n))
    # speech never pauses -> forced cuts
    s = Segmenter(chunk=2, use_vad=True, vad_fn=lambda b: [(0, len(b))])
    flags = []
    for _ in s.segments(frames(10)):
        flags.append((s.last_forced, s.last_pause))
    assert flags and flags[0] == (True, 0.0)
    # 1 s of speech then silence -> cut at a pause, with the silence length reported
    s2 = Segmenter(chunk=8, use_vad=True, vad_fn=lambda b: [(0, min(len(b), sr))])
    got = []
    for _ in s2.segments(frames(6)):
        got.append((s2.last_forced, round(s2.last_pause, 1)))
        break
    assert got == [(False, 0.5)] or got[0][0] is False and got[0][1] >= 0.6
