"""#280: AVX2 minimum-spec check and the slow-machine warning (behaviour, mocked cpuinfo / timings)."""
import pytest

from tinglongwu import cli
from tinglongwu.minspec import SlowWatch, check_avx2, cpu_has_avx2


def _cpuinfo(tmp_path, flags):
    p = tmp_path / "cpuinfo"
    p.write_text(f"processor : 0\nmodel name : Test CPU\nflags : {flags}\n")
    return str(p)


def test_avx2_detected_from_cpuinfo(tmp_path):
    assert cpu_has_avx2("linux", _cpuinfo(tmp_path, "fpu sse4_2 avx avx2 fma")) is True
    assert cpu_has_avx2("linux", _cpuinfo(tmp_path, "fpu sse4_2 avx")) is False  # "avx" alone is not avx2


def test_avx2_unknown_never_blocks(tmp_path):
    assert cpu_has_avx2("linux", str(tmp_path / "missing")) is None
    p = tmp_path / "arm"
    p.write_text("Features : fp asimd\n")
    assert cpu_has_avx2("linux", str(p)) is None
    check_avx2(has_avx2=lambda: None)  # no exception


def test_avx2_windows_uses_os_answer():
    assert cpu_has_avx2("win32", win=lambda: ("x", ["AVX2"])) is True
    assert cpu_has_avx2("win32", win=lambda: ("x", [])) is False
    assert cpu_has_avx2("win32", win=lambda: ("x", None)) is None


def test_check_avx2_exits_clearly_and_force_continues():
    with pytest.raises(RuntimeError, match="not supported: CPU lacks AVX2"):
        check_avx2(has_avx2=lambda: False)
    check_avx2(force=True, has_avx2=lambda: False)
    check_avx2(has_avx2=lambda: True)


def test_cli_exit_code_2_without_avx2(monkeypatch, capsys):
    monkeypatch.setattr("tinglongwu.minspec.cpu_has_avx2", lambda *a, **k: False)
    monkeypatch.setattr("tinglongwu.minspec.check_avx2.__defaults__", (False, lambda: False))
    assert cli.main(["--file", "x.wav"]) == 2
    assert "not supported: CPU lacks AVX2" in capsys.readouterr().err


def test_force_flag_parsed():
    assert cli.parse_args(["--force"]).force is True
    assert cli.parse_args([]).force is False


def test_slow_warning_threshold():
    msgs = []
    w = SlowWatch(say=msgs.append)
    # median work 4.1 s on 8 s segments: exactly > 0.5 x 8 = 4.0 -> warn, but only after 3 segments
    assert w.observe(3.0, 1.1, 8.0) is False
    assert w.observe(3.0, 1.1, 8.0) is False
    assert msgs == []
    assert w.observe(3.0, 1.1, 8.0) is True
    assert len(msgs) == 1 and "below the recommended spec" in msgs[0]
    assert w.observe(3.0, 1.1, 8.0) is False  # one time only
    assert len(msgs) == 1


def test_no_warning_at_or_under_half():
    msgs = []
    w = SlowWatch(say=msgs.append)
    for _ in range(10):
        w.observe(3.0, 1.0, 8.0)  # 4.0 s on 8 s: not greater than half
    assert msgs == []


def test_median_ignores_one_slow_outlier():
    msgs = []
    w = SlowWatch(say=msgs.append)
    for work in (1.0, 20.0, 1.0):
        w.observe(work, 0.0, 8.0)
    assert msgs == []
