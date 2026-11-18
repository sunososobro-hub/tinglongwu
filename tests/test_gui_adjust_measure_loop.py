"""#1556: opening the Adjust panel must not re-grid in reaction to its own measuring (macOS Tk delivers
<Configure> synchronously inside update_idletasks())."""
from tinglongwu.gui.app import App


class FakePanel:
    def __init__(self, app):
        self.app, self.depth, self.geo, self.mins = app, 0, None, None

    def update_idletasks(self):
        # Like Tk on macOS: the stale Configure event is delivered inside this call.
        self.depth += 1
        if self.depth > 200:
            raise RecursionError("maximum recursion depth exceeded")
        try:
            self.app._adv_width_changed(525 if self.app.setting_cols == 1 else 404)
        finally:
            self.depth -= 1

    def winfo_reqwidth(self):
        return 400

    def winfo_reqheight(self):
        return 600 if self.app.setting_cols == 2 else 700

    def winfo_width(self):
        return 758

    def winfo_height(self):
        return 682

    def state(self):
        return "withdrawn"

    def deiconify(self):
        pass

    def lift(self):
        pass

    def minsize(self, w, h):
        self.mins = (w, h)

    def geometry(self, g):
        self.geo = g


class FakeFrm:
    def winfo_ismapped(self):
        return True

    def winfo_reqwidth(self):
        return 500


class FakeApp:
    toggle_adjust = App.toggle_adjust
    _adv_width_changed = App._adv_width_changed
    _apply_panel_geometry = App._apply_panel_geometry

    def __init__(self):
        self.setting_cols, self.two_col_w = 2, 0
        self.regrids = []
        self.panel = FakePanel(self)
        self.frm = FakeFrm()
        self.settings = {"panel_w": 758, "panel_h": 682}

    def _set_setting_cols(self, cols):
        if cols == self.setting_cols:
            return False
        self.setting_cols = cols
        self.regrids.append(cols)
        return True


def test_opening_panel_regrids_only_for_measuring():
    app = FakeApp()
    app._apply_panel_geometry()  # RecursionError before the fix
    assert app.regrids == [1, 2]
    assert app.panel.geo == "758x682"
    assert app._measuring_panel is False


def test_width_changes_still_switch_columns_after_measuring():
    app = FakeApp()
    app._apply_panel_geometry()
    app.panel.update_idletasks = lambda: None
    app._adv_width_changed(100)
    assert app.setting_cols == 1
    app._adv_width_changed(900)
    assert app.setting_cols == 2


def test_toggle_adjust_opening_flush_is_guarded_too():
    app = FakeApp()
    app.setting_cols = 1  # panel last left in one column; the opening flush delivers a stale Configure
    app.toggle_adjust()
    assert app.regrids == [2]  # only the measuring re-grid (1 is already set, then 2); no reaction to Configure
    assert app.panel.geo == "758x682"
