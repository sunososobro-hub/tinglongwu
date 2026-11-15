"""#939: the Adjust panel is a normal resizable window with a menu bar; its size is remembered."""
import pytest

from test_gui_density_886 import small_screen_app  # noqa: F401  (fixture)


def test_both_windows_resizable_with_menu(small_screen_app):  # noqa: F811
    a = small_screen_app()
    assert a.root.resizable() == (True, True)
    assert a.panel.resizable() == (True, True)
    assert a.panel.cget("menu")
    assert a.panel.winfo_reqheight() <= 600


def test_grid_has_two_columns_at_default_width(small_screen_app):  # noqa: F811
    a = small_screen_app()
    a.set_advanced(True)
    a.root.update()
    cols = {c.grid_info()["column"] for c in a.frm.winfo_children() if c.grid_info() and c.grid_info()["row"] < 6}
    assert {0, 4} <= cols


def test_panel_size_is_remembered(small_screen_app):  # noqa: F811
    a = small_screen_app()
    w, h = a.panel.winfo_reqwidth(), a.panel.winfo_reqheight()
    a.panel.geometry(f"{w + 120}x{h + 60}+30+40")
    a.panel.update()
    a.collect()
    assert a.settings["panel_w"] >= w + 100 and a.settings["panel_h"] >= h + 50
    from tinglongwu.gui.core import DEFAULTS
    assert {"panel_w", "panel_h", "panel_x", "panel_y"} <= set(DEFAULTS)
