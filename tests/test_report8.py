import io

import numpy as np

from tests.test_stream import FakeASR, FakeMT, Frames, make, tone
from tinglongwu import device
from tinglongwu.asr import Recognizer, Transcript
from tinglongwu.hallucination import is_hallucination
from tinglongwu.pipeline import run
from tinglongwu.segment import Segmenter
from tinglongwu.sink import TextSink
from tinglongwu.stream import DraftEngine, DraftRenderer
from tinglongwu.translate import zh_convert


# ---- hallucination filter ----
def test_strong_phrases_dropped_even_with_normal_energy():
    for t in ["Продолжение следует...", "Субтитры сделал DimaTorzok", "Редактор субтитров А.Семкин",
              "Subtitles by the Amara.org community", "字幕由 Amara.org 社群提供", "Fortsetzung folgt..."]:
        assert is_hallucination(t, rms=0.1), t


def test_weak_phrases_need_low_energy_or_no_speech():
    for t in ["Спасибо за просмотр!", "Thanks for watching!", "ご視聴ありがとうございました", "請不吝點贊 訂閱"]:
        assert not is_hallucination(t, rms=0.1, no_speech=0.0), t
        assert is_hallucination(t, rms=0.002), t
        assert is_hallucination(t, rms=0.1, no_speech=0.5), t


def test_real_speech_is_kept():
    assert not is_hallucination("Мы смотрим фильм, продолжение следует в субботу после обеда у друзей", rms=0.1)
    assert not is_hallucination("А как ты?", rms=0.001)
    assert not is_hallucination("", rms=0.0)


def test_pipeline_drops_hallucinated_final():
    out = io.StringIO()

    class ASR(Recognizer):
        def transcribe(self, audio):
            return Transcript("Продолжение следует...", "ru")

    seg = Segmenter(chunk=1.0, use_vad=False, overlap=0.1)
    run(Frames(4), seg, ASR(), FakeMT(), TextSink(out), "zh", None)
    assert out.getvalue() == ""


def test_draft_skips_hallucination():
    eng, clock, state, out, mt = make(["Субтитры сделал DimaTorzok"], lang="ru")
    eng.tick()
    eng.translate_pending()
    assert mt.drafts == [] and out.getvalue() == ""


# ---- 妳 -> 你 ----
def test_zh_convert_normalises_ni():
    assert "妳" not in zh_convert("妳好嗎？我愛妳們。", "tw")
    assert zh_convert("妳好", "tw").startswith("你好")
    assert zh_convert("妳好", "cn").startswith("你好")


# ---- draft / stderr collision ----
def test_latency_log_does_not_share_row_with_draft():
    out = io.StringIO()
    logs = []

    def log(m):  # stderr and stdout share the terminal: record what is on screen when the line is written
        logs.append((m, renderer.rows))

    renderer = DraftRenderer(out, columns=lambda: 80)
    eng = DraftEngine(FakeASR(["privet mir"], "ru"), FakeMT(), renderer, lambda: (0, tone()), "zh",
                      clock=lambda: 0.0, log=log)
    eng.tick()
    eng.translate_pending()
    assert logs and "first zh draft" in logs[0][0] and logs[0][1] == 0  # draft wiped before the log line
    assert renderer.rows == 1  # ... and redrawn after it
    eng.final_written(0)
    assert logs[1][1] == 0 and renderer.rows == 0


# ---- Whisper compute type on a tight GPU ----
def test_asr_compute_type_by_free_vram():
    assert device.asr_compute_type("cpu") == "int8"
    assert device.asr_compute_type("cuda", free_mib=7600) == "int8_float16"
    assert device.asr_compute_type("cuda", free_mib=24000) == "float16"
    assert device.asr_compute_type("cuda", free_mib=24000) == "float16"


def test_asr_compute_env_override(monkeypatch):
    monkeypatch.setenv("TLWU_ASR_COMPUTE", "float32")
    assert device.asr_compute_type("cuda", free_mib=7600) == "float32"


def test_zh_subtitle_by_name_is_hallucination():
    assert is_hallucination('字幕by索兰娅')
    assert not is_hallucination('這個字幕by default 是開的，我們討論一下設定')

