import wer

from tinglongwu.capture import (FileSource, MacLoopbackSource, PulseMonitorSource, WasapiLoopbackSource,
                         select_backend)


def test_wer_basic():
    r = wer.score("the cat sat on the mat", "the cat sat on a mat", "en")
    assert r["metric"] == "WER" and abs(r["rate"] - 1 / 6) < 1e-9


def test_cer_zh():
    r = wer.score("今天天气很好", "今天天汽很好", "zh")
    assert r["metric"] == "CER" and abs(r["rate"] - 1 / 6) < 1e-9


def test_partial_alignment():
    ref = "one two three four five six seven eight nine ten"
    r = wer.score(ref, "five six sevn eight", "en", partial=True)
    assert r["window"] == (4, 8) and r["errors"] == 1
    assert abs(r["coverage"] - 0.4) < 1e-9


def test_backend_selection(tmp_path):
    assert isinstance(select_backend(platform="win32"), WasapiLoopbackSource)
    assert isinstance(select_backend(platform="linux"), PulseMonitorSource)
    assert isinstance(select_backend(platform="darwin"), MacLoopbackSource)
    assert isinstance(select_backend(file="x.wav", platform="linux"), FileSource)


def test_file_source_resamples(wav):
    import numpy as np

    audio = np.concatenate(list(FileSource(str(wav)).frames()))
    assert audio.dtype == np.float32 and audio.ndim == 1 and abs(len(audio) - 3 * 16000) < 50
