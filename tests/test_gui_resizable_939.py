"""#939: the Adjust panel is a normal resizable window with a menu bar; its size is remembered."""

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


def _slot_cols(a):
    """Grid columns used by the visible setting labels (slot column offset 0)."""
    return {w.grid_info()["column"] for items in a.slot_widgets.values() for w, dcol in items
            if dcol == 0 and w.winfo_manager() == "grid"}


def _resize(a, w, h):
    a.panel.geometry(f"{w}x{h}")
    a.panel.update()
    a.panel.update()


def test_grid_wraps_to_one_column_below_two_column_width(small_screen_app):  # noqa: F811
    a = small_screen_app()
    a.set_advanced(True)
    a.root.update()
    assert _slot_cols(a) == {0, 4}
    mw, mh = a.panel.minsize()
    assert mw < a.panel.winfo_reqwidth()  # the window can be dragged narrower than the two-column layout
    _resize(a, mw, mh)
    assert a.setting_cols == 1 and _slot_cols(a) == {0}
    rows = sorted(w.grid_info()["row"] for items in a.slot_widgets.values() for w, d in items
                  if d == 0 and w.winfo_manager() == "grid")
    assert len(rows) == len(set(rows))  # one setting per row, none stacked on another
    assert a.panel.winfo_height() >= a.panel.winfo_reqheight()  # the taller one-column grid is not cut off
    _resize(a, a.two_col_w + 300, mh)
    assert a.setting_cols == 2 and _slot_cols(a) == {0, 4}


def test_wrap_keeps_hidden_settings_hidden(small_screen_app):  # noqa: F811
    a = small_screen_app()
    a.set_advanced(True)
    hidden = [w for s in (0, 1) for w, _ in a.slot_widgets[s]]  # source / target live on the home page
    assert hidden and all(w.winfo_manager() == "" for w in hidden)
    _resize(a, *a.panel.minsize())
    assert all(w.winfo_manager() == "" for w in hidden)
