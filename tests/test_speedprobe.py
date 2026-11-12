"""#79: translation speed probe - owner rule (CPU keeps OPUS unless Hy-MT2 median < 1.0 s), budget, cache.
Behaviour tests with fake translators and an injected clock (no model needed)."""
from __future__ import annotations

import json

import pytest

from tinglongwu import cli, hwprobe, speedprobe
from tinglongwu.hwprobe import HwInfo, recommend

CPU = HwInfo(None, None, 16000, 8)
GPU = HwInfo("RTX 3050", 8192, 16000, 8)


class Clock:
    """Fake time: every translate() call advances it by the next value of `per` (last value repeats)."""

    def __init__(self, per):
        self.per, self.now, self.calls = list(per), 0.0, 0

    def __call__(self):
        return self.now

    def translate(self, s):
        self.now += self.per[min(self.calls, len(self.per) - 1)]
        self.calls += 1


def maker(clock, seen=None):
    def make(models_dir, src, tgt):
        if seen is not None:
            seen.append((src, tgt))
        return clock.translate
    return make


def test_time_mt_skips_warmup_and_stops_at_budget():
    c = Clock([5.0, 0.5])  # slow first call = model warm-up, must not count
    assert speedprobe.time_mt(c.translate, clock=c) == [0.5] * (len(speedprobe.SENTENCES) - 1)
    c = Clock([1.0, 4.0])  # 1 + 4 + 4 + 4 = 13 s >= 12 s budget -> stop after 3 timed sentences
    assert speedprobe.time_mt(c.translate, clock=c) == [4.0, 4.0, 4.0]
    assert c.calls == 4


@pytest.mark.parametrize("per,mt", [(0.93, "hymt2"), (0.99, "hymt2"), (1.0, "opus"), (2.5, "opus")])
def test_cpu_tier_uses_hymt_only_under_one_second_median(tmp_path, per, mt):
    c = Clock([3.0, per])
    r = speedprobe.run(CPU, recommend(CPU), tmp_path, path=tmp_path / "sp.json", make=maker(c), clock=c)
    assert r.mt == mt and r.hymt_median_s == pytest.approx(per) and not r.cached
    assert speedprobe.apply(recommend(CPU), r).mt == mt


def test_very_slow_machine_keeps_opus_without_waiting_for_all_sentences(tmp_path):
    c = Clock([10.0, 9.0])  # budget is gone after the first timed sentence
    r = speedprobe.run(CPU, recommend(CPU), tmp_path, path=tmp_path / "sp.json", make=maker(c), clock=c)
    assert r.mt == "opus" and r.hymt_median_s is None and c.calls == 2


def test_result_is_cached_until_hardware_changes_or_forced(tmp_path):
    path, c = tmp_path / "cfg" / "sp.json", Clock([1.0, 0.5])
    first = speedprobe.run(CPU, recommend(CPU), tmp_path, path=path, make=maker(c), clock=c)
    assert first.mt == "hymt2" and json.loads(path.read_text("utf-8"))["mt"] == "hymt2"
    slow = Clock([1.0, 3.0])
    again = speedprobe.run(CPU, recommend(CPU), tmp_path, path=path, make=maker(slow), clock=slow)
    assert again.cached and again.mt == "hymt2" and slow.calls == 0  # no second measurement
    more_ram = HwInfo(None, None, 32000, 8)
    changed = speedprobe.run(more_ram, recommend(more_ram), tmp_path, path=path, make=maker(slow), clock=slow)
    assert not changed.cached and changed.mt == "opus"
    c2 = Clock([1.0, 0.5])
    forced = speedprobe.run(more_ram, recommend(more_ram), tmp_path, path=path, make=maker(c2), clock=c2, force=True)
    assert not forced.cached and forced.mt == "hymt2"


def test_corrupt_cache_is_ignored(tmp_path):
    path = tmp_path / "sp.json"
    path.write_text("{not json", "utf-8")
    assert speedprobe.load_cached(CPU, path) is None
    path.write_text(json.dumps({"hw": speedprobe.signature(CPU), "mt": "qwen"}), "utf-8")
    assert speedprobe.load_cached(CPU, path) is None


def test_gpu_tier_and_low_ram_do_not_run_the_test(tmp_path):
    def boom(*a):
        raise AssertionError("must not load Hy-MT2")
    r = speedprobe.run(GPU, recommend(GPU), tmp_path, path=tmp_path / "a.json", make=boom)
    assert r.mt == "hymt2" and r.hymt_median_s is None
    small = HwInfo(None, None, 3000, 4)
    r = speedprobe.run(small, recommend(small), tmp_path, path=tmp_path / "b.json", make=boom)
    assert r.mt == "opus" and "RAM" in r.note


def test_missing_model_keeps_opus_and_is_not_cached(tmp_path):
    path = tmp_path / "sp.json"
    r = speedprobe.run(CPU, recommend(CPU), tmp_path / "no-models", path=path)  # real loader, no GGUF on disk
    assert r.mt == "opus" and "not tested" in r.note and not path.exists()


def test_cli_probe_speed_prints_measured_choice(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(hwprobe, "probe", lambda: CPU)
    monkeypatch.setattr(speedprobe, "cache_path", lambda: tmp_path / "sp.json")
    c = Clock([2.0, 0.4])
    monkeypatch.setattr(speedprobe, "_hymt_cpu", maker(c))
    monkeypatch.setattr(speedprobe, "_clock", c)
    assert cli.main(["--probe", "--speed", "--models-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "translation hymt2" in out and "median 0.40 s/sentence" in out and "(remembered)" not in out
    assert cli.main(["--probe", "--speed", "--models-dir", str(tmp_path)]) == 0
    assert "Speed test (remembered)" in capsys.readouterr().out
    assert cli.main(["--probe"]) == 0
    assert "translation opus" in capsys.readouterr().out  # plain --probe is unchanged
