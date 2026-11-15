"""Load-time breakdown is not shown in the main window; only a slow load (>10 s) gets a friendly line."""
from tinglongwu.gui.core import ready_extra_line


def test_fast_load_has_no_extra_line():
    assert ready_extra_line("listening") == ""
    assert ready_extra_line("listening - loaded in 4.9 s (speech model 3.5 s, translation 1.4 s)") == ""


def test_slow_load_shows_friendly_line():
    assert ready_extra_line("listening - loaded in 14.2 s (speech model 12.0 s, translation 2.2 s)")
    assert ready_extra_line("listening - loaded in 10.0 s") == ""
