"""#1773: CPU vs Vulkan speed check after the Vulkan pack is installed; the faster backend is kept and cached."""
from __future__ import annotations

import json
import subprocess
import types

import pytest

from tinglongwu import backend_probe, cli, vulkan_probe

KEY = {"app": "0.5.4", "driver": "d1", "pack": "p1"}


def _timer(cpu, vk, calls=None):
    def t(b):
        if calls is not None:
            calls.append(b)
        v = {"cpu": cpu, "vulkan": vk}[b]
        if isinstance(v, Exception):
            raise v
        return v
    return t


def test_vulkan_faster_keeps_vulkan_for_both_stages(tmp_path):
    path, lines = tmp_path / "p.json", []
    r = vulkan_probe.ensure(_timer(2.76, 1.58), KEY, env={}, path=path, say=lines.append)
    assert r.winner == "vulkan" and "Vulkan 1.58 s vs CPU 2.76 s" in lines[0]
    assert vulkan_probe.vulkan_allowed(env={}, key=KEY, path=path)  # gate used by MT (load_llama) and ASR
    seen = {}

    def select(mdir, size, lang, *, vulkan, **kw):
        seen["vulkan"] = vulkan
        return types.SimpleNamespace(device="vulkan"), "vulkan"

    a = types.SimpleNamespace(compute="auto", model="small", lang="ru")
    rec, be = cli.live_recognizer(a, tmp_path, "cpu", select=select, ready=lambda *_: True, vulkan=lambda: True,
                                  env={}, say=lambda m: None,
                                  allowed=lambda: vulkan_probe.vulkan_allowed({}, KEY, path))
    assert seen["vulkan"] is True and be == "vulkan"


def test_cpu_faster_picks_cpu_with_both_timings(tmp_path):
    path, lines = tmp_path / "p.json", []
    r = vulkan_probe.ensure(_timer(2.10, 2.70), KEY, env={}, path=path, say=lines.append)
    assert r.winner == "cpu" and len(lines) == 1 and "CPU 2.10 s vs Vulkan 2.70 s: using CPU" in lines[0]
    assert not vulkan_probe.vulkan_allowed(env={}, key=KEY, path=path)
    seen = {}

    def select(mdir, size, lang, *, vulkan, **kw):
        seen["vulkan"] = vulkan
        return types.SimpleNamespace(device="cpu"), "cpu"

    a = types.SimpleNamespace(compute="auto", model="small", lang="ru")
    cli.live_recognizer(a, tmp_path, "cpu", select=select, ready=lambda *_: True, vulkan=lambda: True, env={},
                        say=lambda m: None, allowed=lambda: vulkan_probe.vulkan_allowed({}, KEY, path))
    assert seen["vulkan"] is False


@pytest.mark.parametrize("cpu,vk", [(subprocess.TimeoutExpired("tlwu", 240), 1.0),
                                    (2.0, RuntimeError("Vulkan did not load")), (2.0, OSError("crash"))])
def test_probe_error_or_timeout_keeps_vulkan_and_says_so(tmp_path, cpu, vk):
    path, lines = tmp_path / "p.json", []
    r = vulkan_probe.ensure(_timer(cpu, vk), KEY, env={}, path=path, say=lines.append)
    assert r.winner == "vulkan" and r.error
    assert "speed check failed" in lines[0] and "keeping Vulkan" in lines[0]
    assert vulkan_probe.vulkan_allowed(env={}, key=KEY, path=path)


@pytest.mark.parametrize("dev", ["cpu", "vulkan", "cuda"])
def test_tlwu_device_overrides_the_probe(tmp_path, dev):
    path, calls, lines = tmp_path / "p.json", [], []
    vulkan_probe.save(KEY, vulkan_probe.decide(1.0, 9.0), path)  # cached: CPU faster
    assert vulkan_probe.ensure(_timer(1, 1, calls), KEY, env={"TLWU_DEVICE": dev}, path=path,
                               say=lines.append) is None
    assert calls == [] and "TLWU_DEVICE=" + dev in lines[0]
    assert vulkan_probe.vulkan_allowed(env={"TLWU_DEVICE": dev}, key=KEY, path=path)  # cache ignored


def test_cached_per_version_driver_pack_not_rerun_on_start(tmp_path):
    path, calls = tmp_path / "p.json", []
    vulkan_probe.ensure(_timer(2.0, 1.0, calls), KEY, env={}, path=path, say=lambda m: None)
    vulkan_probe.ensure(_timer(2.0, 1.0, calls), dict(KEY), env={}, path=path, say=lambda m: None)
    assert calls == ["cpu", "vulkan"]  # second start: cache hit, nothing timed
    for change in ({"app": "0.5.5"}, {"driver": "d2"}, {"pack": "p2"}):
        vulkan_probe.ensure(_timer(2.0, 1.0, calls), {**KEY, **change}, env={}, path=path, say=lambda m: None)
    assert calls.count("cpu") == 4  # an update, a driver change and a re-installed pack each time again


def test_driver_id_changes_with_the_icd_driver(tmp_path):
    icd = tmp_path / "icd.d"
    icd.mkdir()
    lib = tmp_path / "libvulkan_radeon.so"
    lib.write_bytes(b"x")
    (icd / "radeon_icd.json").write_text(json.dumps({"ICD": {"library_path": str(lib)}}))
    env = {"VK_ICD_FILENAMES": str(icd / "radeon_icd.json")}
    a = vulkan_probe.driver_id(env, "linux", tmp_path)
    lib.write_bytes(b"xy")  # driver update
    b = vulkan_probe.driver_id(env, "linux", tmp_path)
    assert a and b and a != b
    assert vulkan_probe.driver_id({}, "linux", tmp_path / "none") == vulkan_probe.driver_id({}, "linux", tmp_path / "none")


def test_cache_key_uses_pack_version_file(tmp_path):
    (tmp_path / "version.txt").write_text("0.5.4")
    k1 = vulkan_probe.cache_key(tmp_path, version="0.5.4", driver=lambda: "d")
    (tmp_path / "version.txt").write_text("0.5.4\n")  # re-installed pack
    k2 = vulkan_probe.cache_key(tmp_path, version="0.5.4", driver=lambda: "d")
    assert k1["app"] == "0.5.4" and k1["driver"] == "d" and k1 != k2


def test_summary_shows_the_last_result(tmp_path):
    path = tmp_path / "p.json"
    assert vulkan_probe.summary(path) == "last GPU speed check: none yet"
    vulkan_probe.save(KEY, vulkan_probe.decide(2.1, 2.7), path)
    assert vulkan_probe.summary(path).startswith("last GPU speed check: GPU speed check: CPU 2.10 s vs Vulkan 2.70 s")


def test_maybe_probe_skips_without_vulkan_or_with_cuda(tmp_path):
    a = types.SimpleNamespace(compute="auto", model="small")
    boom = lambda b: pytest.fail("timed")  # noqa: E731
    assert backend_probe.maybe_probe(a, tmp_path, "cuda", env={}, available=lambda: True, timer=boom) is None
    assert backend_probe.maybe_probe(a, tmp_path, "cpu", env={}, available=lambda: False, timer=boom) is None
    a.compute = "cpu"
    assert backend_probe.maybe_probe(a, tmp_path, "cpu", env={}, available=lambda: True, timer=boom) is None


def test_maybe_probe_runs_once_then_remembers(tmp_path, monkeypatch):
    monkeypatch.setattr(vulkan_probe, "driver_id", lambda *a, **k: "d")
    a = types.SimpleNamespace(compute="auto", model="small")
    path, lines, calls = tmp_path / "p.json", [], []
    r = backend_probe.maybe_probe(a, tmp_path, "cpu", env={}, available=lambda: True, timer=_timer(1.0, 2.0, calls),
                                  path=path, say=lines.append)
    assert r.winner == "cpu" and calls == ["cpu", "vulkan"]
    r2 = backend_probe.maybe_probe(a, tmp_path, "cpu", env={}, available=lambda: True, timer=_timer(1, 2, calls),
                                   path=path, say=lines.append)
    assert r2.winner == "cpu" and calls == ["cpu", "vulkan"] and lines[-1].endswith("(remembered)")


def test_time_here_times_both_stages_and_child_output_parses():
    t = iter([0, 1, 1.5, 10, 10.4, 10.6, 20, 20.5, 20.8])  # warm-up, then two timed passes
    out = []
    rec = types.SimpleNamespace(transcribe=lambda audio: None)
    tr = types.SimpleNamespace(translate=lambda *a: None)
    d = backend_probe.time_here("vulkan", ".", "small", make_asr=lambda: (rec, "vulkan"),
                                make_mt=lambda: (tr, "vulkan"), clip=lambda: [0.0], clock=lambda: next(t),
                                out=out.append)
    assert (d["asr_s"], d["mt_s"]) == (0.4, 0.2)  # best timed pass, not the warm-up
    assert backend_probe.parse_child("noise\n" + out[0]) == pytest.approx(0.6)


def test_time_here_fails_when_vulkan_not_used():
    rec = types.SimpleNamespace(transcribe=lambda audio: None)
    tr = types.SimpleNamespace(translate=lambda *a: None)
    with pytest.raises(RuntimeError, match="Vulkan did not load"):
        backend_probe.time_here("vulkan", ".", "small", make_asr=lambda: (rec, "cpu"), make_mt=lambda: (tr, "cpu"),
                                clip=lambda: [0.0], out=lambda m: None)


def test_time_in_child_sets_device_and_reports_failures(tmp_path):
    seen = {}

    def run(cmd, env, **kw):
        seen.update(cmd=cmd, dev=env["TLWU_DEVICE"], timeout=kw["timeout"])
        return types.SimpleNamespace(returncode=0, stdout=backend_probe.RESULT_PREFIX + '{"asr_s": 1, "mt_s": 0.5}',
                                     stderr="")
    assert backend_probe.time_in_child("vulkan", "small", tmp_path, run=run) == 1.5
    assert seen["dev"] == "vulkan" and "--time-backend" in seen["cmd"] and seen["timeout"] > 0
    bad = lambda cmd, env, **kw: types.SimpleNamespace(returncode=1, stdout="", stderr="x\nmodel missing")  # noqa: E731
    with pytest.raises(RuntimeError, match="model missing"):
        backend_probe.time_in_child("cpu", "small", tmp_path, run=bad)


def test_time_backend_flag_is_parsed():
    a = cli.parse_args(["--time-backend", "cpu", "--model", "small"])
    assert a.time_backend == "cpu" and a.model == "small"
