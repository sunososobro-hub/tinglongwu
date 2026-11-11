"""`--engine auto` on CPU (CLI and GUI, which passes --engine auto). #276 (owner 11/11) replaced the #131 speed-test
gate: Hy-MT2 is the engine on every CPU (~0.93 s/sentence on an i5-13500 accepted), whatever the probe measured.
`tlwu --probe --speed` still reports the measurement."""
from __future__ import annotations

import pytest

from tinglongwu import hwprobe, speedprobe
from tinglongwu.hwprobe import HwInfo, recommend
from tinglongwu.hymt import hymt_path
from tinglongwu.qwen import choose_engine

CPU = HwInfo(None, None, 16000, 8)
GPU = HwInfo("RTX 3050", 8192, 16000, 8)


def _gguf(mdir):
    p = hymt_path(mdir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")


def _probe(tmp_path, hw, per_sentence):
    """Run the real speed probe with a fake translator taking `per_sentence` seconds; cache in tmp_path."""
    now = [0.0]

    def make(*_a):
        def tr(_s):
            now[0] += per_sentence
        return tr

    cache = tmp_path / "speedprobe.json"
    speedprobe.run(hw, recommend(hw), tmp_path, path=cache, make=make, clock=lambda: now[0])
    return cache


def _auto_cpu(mdir, hw, cache):
    return choose_engine("auto", "cpu", mdir, cpu_choice=lambda: speedprobe.cpu_choice(hw, cache))


@pytest.mark.parametrize("per_sentence", [0.9, 2.5])
def test_cpu_auto_is_hymt_whatever_the_probe_measured(tmp_path, per_sentence):
    _gguf(tmp_path)
    name, why = _auto_cpu(tmp_path, CPU, _probe(tmp_path, CPU, per_sentence))
    assert name == "hymt2-cpu" and "CPU" in why


def test_cpu_auto_is_hymt_without_any_probe(tmp_path, monkeypatch):
    """No injection and nothing measured: the default path never runs or needs the speed test."""
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setattr(hwprobe, "probe", lambda *a, **k: (_ for _ in ()).throw(AssertionError("probed")))
    assert choose_engine("auto", "cpu", tmp_path)[0] == "hymt2-cpu"


def test_explicit_opus_still_honoured(tmp_path):
    _gguf(tmp_path)
    cache = _probe(tmp_path, CPU, 0.9)
    assert choose_engine("opus", "cpu", tmp_path, cpu_choice=lambda: speedprobe.cpu_choice(CPU, cache))[0] == "opus"


def test_speed_probe_still_reports(tmp_path):
    cache = _probe(tmp_path, GPU, 0.1)
    assert speedprobe.load_cached(GPU, cache).mt == "hymt2"

    def boom():
        raise OSError("no wmi")

    assert speedprobe.cpu_choice(probe=boom) is None
