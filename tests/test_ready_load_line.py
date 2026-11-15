"""Slow-load line: shown WHILE loading once 10 s have passed, never after ready (0.3.8 review round 2)."""
from tinglongwu.gui.core import SLOW_LOAD_SECS, slow_load_line


def test_before_threshold_no_line():
    assert slow_load_line(0) == "" and slow_load_line(SLOW_LOAD_SECS - 0.1) == ""


def test_after_threshold_friendly_line():
    assert slow_load_line(SLOW_LOAD_SECS)
    assert slow_load_line(42.0) == slow_load_line(10.0)


def test_ready_path_in_app_does_not_add_the_line():
    import inspect

    from tinglongwu.gui import app

    src = inspect.getsource(app)
    assert "ready_extra_line" not in src and "_slow_clear()" in src
