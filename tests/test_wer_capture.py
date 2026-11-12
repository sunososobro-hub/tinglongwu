import pytest
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


def test_mac_capture_prefers_blackhole_2ch_not_16ch():
    from tinglongwu.capture.macos import pick_blackhole
    names = ["MacBook Pro Microphone", "BlackHole 16ch", "BlackHole 2ch"]
    assert pick_blackhole(names) == "BlackHole 2ch"           # 16ch listed first must not win
    assert pick_blackhole(["BlackHole 16ch", "BlackHole 64ch", "BlackHole 3ch"]) == "BlackHole 3ch"
    assert pick_blackhole(names, "16ch") == "BlackHole 16ch"  # explicit --capture-device wins
    with pytest.raises(RuntimeError, match="blackhole-2ch"):
        pick_blackhole(["MacBook Pro Microphone", "BlackHole 16ch"])
    with pytest.raises(RuntimeError, match="matching"):
        pick_blackhole(names, "nothing")


def test_mac_source_picks_device_through_soundcard():
    import types

    import tinglongwu.capture._soundcard as scm
    seen = []
    mics = [types.SimpleNamespace(name=n) for n in ("MacBook Pro Microphone", "BlackHole 16ch", "BlackHole 2ch")]
    sc = types.SimpleNamespace(all_microphones=lambda include_loopback=True: (seen.append(include_loopback), mics)[1])
    assert MacLoopbackSource()._pick_microphone(sc).name == "BlackHole 2ch"
    assert scm.find_by_name is not None and seen and not any(seen)
