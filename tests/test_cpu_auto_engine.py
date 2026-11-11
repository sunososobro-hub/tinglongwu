"""#131: `--engine auto` on CPU (CLI and GUI, which passes --engine auto) uses the remembered speed test:
Hy-MT2 only when `tlwu --probe --speed` measured it under the owner's 1.0 s/sentence rule on this hardware."""
from __future__ import annotations

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


def test_fast_cpu_probe_makes_auto_pick_hymt(tmp_path):
    _gguf(tmp_path)
    cache = _probe(tmp_path, CPU, 0.9)
    name, why = _auto_cpu(tmp_path, CPU, cache)
    assert name == "hymt2" and "0.90" in why


def test_slow_cpu_probe_keeps_opus(tmp_path):
    _gguf(tmp_path)
    assert _auto_cpu(tmp_path, CPU, _probe(tmp_path, CPU, 2.5))[0] == "opus"


def test_no_cache_keeps_opus(tmp_path):
    _gguf(tmp_path)
    assert _auto_cpu(tmp_path, CPU, tmp_path / "none.json")[0] == "opus"


def test_hardware_change_ignores_cache(tmp_path):
    _gguf(tmp_path)
    cache = _probe(tmp_path, CPU, 0.9)
    assert _auto_cpu(tmp_path, HwInfo(None, None, 32000, 8), cache)[0] == "opus"


def test_gpu_tier_entry_does_not_put_hymt_on_cpu(tmp_path):
    """GPU machine running on CPU (old driver): the cached GPU-tier answer was never timed on the CPU."""
    _gguf(tmp_path)
    cache = _probe(tmp_path, GPU, 0.1)
    assert speedprobe.load_cached(GPU, cache).mt == "hymt2"
    assert _auto_cpu(tmp_path, GPU, cache)[0] == "opus"


def test_probe_chose_hymt_but_gguf_missing_keeps_opus(tmp_path):
    cache = _probe(tmp_path, CPU, 0.9)
    name, why = _auto_cpu(tmp_path, CPU, cache)
    assert name == "opus" and "missing" in why


def test_explicit_engine_ignores_probe(tmp_path):
    _gguf(tmp_path)
    cache = _probe(tmp_path, CPU, 0.9)
    assert choose_engine("opus", "cpu", tmp_path, cpu_choice=lambda: speedprobe.cpu_choice(CPU, cache))[0] == "opus"


def test_default_reads_the_cache_next_to_gui_settings(tmp_path, monkeypatch):
    """No injection: the real default path (APPDATA/tinglongwu/speedprobe.json) and hwprobe.probe()."""
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setattr(hwprobe, "probe", lambda *a, **k: CPU)
    mdir = tmp_path / "models"
    _gguf(mdir)
    assert choose_engine("auto", "cpu", mdir)[0] == "opus"  # nothing measured yet
    now = [0.0]
    speedprobe.run(CPU, recommend(CPU), mdir, make=lambda *_a: (lambda _s: now.__setitem__(0, now[0] + 0.9)),
                   clock=lambda: now[0])
    assert (tmp_path / "appdata" / "tinglongwu" / "speedprobe.json").is_file()
    assert choose_engine("auto", "cpu", mdir)[0] == "hymt2"


def test_broken_probe_keeps_opus(tmp_path):
    _gguf(tmp_path)

    def boom():
        raise OSError("no wmi")

    assert speedprobe.cpu_choice(probe=boom) is None
    assert choose_engine("auto", "cpu", tmp_path, cpu_choice=lambda: speedprobe.cpu_choice(probe=boom))[0] == "opus"
