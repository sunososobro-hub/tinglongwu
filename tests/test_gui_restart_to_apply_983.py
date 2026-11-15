"""#983: a start-time setting changed while running shows "restart to apply" + a one-click restart."""
from __future__ import annotations

import pytest

from tinglongwu.gui.core import START_TIME_SETTINGS, restart_needed


def test_every_start_time_setting_is_reported_when_changed():
    base = {k: "a" for k in START_TIME_SETTINGS}
    assert restart_needed(base, dict(base)) == []
    for k in START_TIME_SETTINGS:
        assert restart_needed(base, {**base, k: "b"}) == [k]


def test_send_mode_is_a_start_time_setting():
    assert restart_needed({"send_mode": "auto"}, {"send_mode": "countdown"}) == ["send_mode"]


class _Alive:
    def is_alive(self):
        return True


@pytest.fixture
def make_app(tmp_path, monkeypatch):
    tk = pytest.importorskip("tkinter")
    try:
        tk.Tk().destroy()
    except tk.TclError:
        pytest.skip("no display")
    from tinglongwu import i18n
    from tinglongwu.gui import app

    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(i18n, "_system_tag", lambda: "en-US")
    monkeypatch.setattr(i18n.userdirs, "write_config_value", lambda k, v: None)
    monkeypatch.setattr(i18n, "_override", "en")
    made = []

    def make():
        a = app.App()
        a.root.update()
        made.append(a)
        return a

    yield make
    for a in made:
        try:
            a.root.destroy()
        except tk.TclError:
            pass


def test_send_mode_change_while_running_shows_notice_and_restart_restarts(make_app, monkeypatch):
    a = make_app()
    a.v_send_mode.set("auto")
    a.worker = _Alive()
    a.run_snapshot = a._start_values()
    a.quick_mode = "speak"
    assert a._restart_check() == []
    assert not a.lbl_restart.winfo_manager()

    a.v_send_mode.set("countdown")  # the trace fires the check
    assert a.lbl_restart.winfo_manager() and a.btn_restart.winfo_manager()
    assert a.lbl_restart.cget("text")

    calls = []
    monkeypatch.setattr(a, "toggle", lambda mode=None: calls.append(mode) or setattr(a, "worker", None))
    a._restart_now()  # stops (worker -> None), then starts the same mode
    a.root.update()
    assert calls == ["speak", "speak"] or calls[-1] == "speak"
    assert a.v_send_mode.get() == "countdown"
