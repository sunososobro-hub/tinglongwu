"""#737 (#687 part 1): the Adjust panel's first screen is Listen / Speak + the other-side language; the rest is Advanced."""
import pytest

INTERACTIVE = ("TButton", "TCombobox", "TCheckbutton", "TRadiobutton", "TEntry", "TSpinbox", "Button", "Checkbutton",
               "Entry", "Spinbox", "Text", "Scale")


@pytest.fixture
def home_app(tmp_path, monkeypatch):
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
    runs = []

    def fake_work(self, s, stop):
        runs.append(dict(s))
        stop.wait(10)
        self.q.put(("stopped",))

    monkeypatch.setattr(app.App, "_work", fake_work)
    (tmp_path / "tinglongwu").mkdir()
    (tmp_path / "tinglongwu" / "gui.json").write_text('{"tour_seen": true}', "utf-8")
    made = []

    def make():
        a = app.App()
        a.root.update()
        made.append(a)
        return a

    yield make, runs
    for a in made:
        a.stop_ev.set()
        try:
            a.root.destroy()
        except tk.TclError:
            pass


def _walk(w):
    for c in w.winfo_children():
        yield c
        yield from _walk(c)


def _outside_advanced(a):
    inside = set(_walk(a.frm)) | {a.frm}
    return [w for w in _walk(a.panel) if w.winfo_class() in INTERACTIVE and w not in inside and w is not a.adv_btn]


def test_fresh_config_shows_three_controls_and_advanced_is_collapsed(home_app):
    make, _ = home_app
    a = make()
    a.toggle_adjust()
    a.root.update()
    assert a.settings["advanced_open"] is False and not a.frm.winfo_ismapped()
    controls = _outside_advanced(a)
    assert sorted(w.winfo_class() for w in controls) == ["TButton", "TButton", "TCombobox"]
    assert all(w.winfo_ismapped() for w in controls)


@pytest.mark.parametrize("mode,source", [("listen", "system"), ("speak", "mic")])
def test_each_big_button_starts_with_its_source(home_app, mode, source):
    make, runs = home_app
    a = make()
    a.quick_btns[mode].invoke()
    a.root.update()
    for _ in range(50):
        if runs:
            break
        a.root.after(20)
        a.root.update()
    assert runs and runs[0]["source"] == source


def test_advanced_open_state_survives_restart(home_app):
    make, _ = home_app
    a = make()
    a.toggle_adjust()
    a.v_adv.set(True)
    a._adv_toggled()
    a.root.update()
    assert a.frm.winfo_ismapped()
    a.collect()
    a.root.destroy()
    b = make()
    b.toggle_adjust()
    b.root.update()
    assert b.settings["advanced_open"] is True and b.frm.winfo_ismapped()
    assert b.combos["engine"].winfo_ismapped()
