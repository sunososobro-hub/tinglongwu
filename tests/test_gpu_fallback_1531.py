"""#1531: first-start GPU self-test failure -> CPU fallback is saved and shown as one plain line."""
import json

import pytest

from tinglongwu import selftest
from tlwu_app import tiers


def _res(downgraded, why="CUDA init error"):
    r = selftest.Result(device="cpu", ok=True, downgraded=downgraded)
    r.steps.append(selftest.Step("GPU", not downgraded, 0.1, why if downgraded else "RTX"))
    return r


def test_no_downgrade_has_no_line():
    assert _res(False).gpu_fallback == "" and "已改用 CPU" not in _res(False).text()


def test_downgrade_line_matches_tiers_and_is_in_output():
    r = _res(True)
    assert r.gpu_fallback == tiers.gpu_fallback_line("CUDA init error")
    assert r.gpu_fallback in r.text()


def test_setup_window_picks_the_line_from_selftest_output():
    setup_gui = pytest.importorskip("tlwu_app.setup_gui")  # needs tkinter
    tail = "stuff\n" + _res(True).text()
    assert setup_gui.gpu_fallback_note(tail) == tiers.gpu_fallback_line("CUDA init error")
    assert setup_gui.gpu_fallback_note("self-test PASSED") == ""


def test_apply_to_gui_saves_fallback_reason(tmp_path):
    p = tmp_path / "gui.json"
    assert selftest.apply_to_gui(_res(True), path=p)
    assert "CUDA init error" in json.loads(p.read_text(encoding="utf-8"))["gpu_fallback"]
    assert selftest.apply_to_gui(_res(False), path=p)
    assert json.loads(p.read_text(encoding="utf-8"))["gpu_fallback"] == ""
