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
    from tinglongwu import vbcable
    from tinglongwu.gui import voice_install

    monkeypatch.setattr(vbcable, "needs_offer", lambda names, platform=None: False)  # #1001: no real dialogs
    monkeypatch.setattr(voice_install, "offer_needed", lambda run, mdir: False)
    # every language pack counts as installed: a missing pack would open the real "Download speech model" askyesno
    monkeypatch.setattr(app.pack_on_demand, "first_missing", lambda *a, **k: None)
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


def _pick_meeting(a):
    """#1343: a fresh install starts in the video (listen only) scene; pick Meeting like a user would."""
    from tinglongwu.gui.core import SCENES

    a.scene_combo.current(SCENES.index("meeting"))
    a.scene_combo.event_generate("<<ComboboxSelected>>")
    a.root.update()
    assert a.settings["scene"] == "meeting"


def test_fresh_config_shows_three_controls_and_advanced_is_collapsed(home_app):
    """Owner 2026-11-15: my language + other side's language + one Start (listen + speak, #1001)."""
    make, _ = home_app
    a = make()
    a.toggle_adjust()
    a.root.update()
    assert a.settings["advanced_open"] is False and not a.frm.winfo_ismapped()
    # #1028: the main window carries my + the other side's language and the one Start (mic / floating toggles beside it)
    assert a.home_target.winfo_ismapped() and a.home_lang.winfo_ismapped() and a.btn.winfo_ismapped()
    assert a.btn is a.both_btn and a.btn.winfo_toplevel() is a.root
    # the settings page is plain language outside Advanced: choices only (radio / check), no dropdowns or entries
    outside = {w.winfo_class() for w in _outside_advanced(a)}
    assert not outside & {"TCombobox", "TEntry", "TSpinbox", "Entry", "Text"}, outside


def test_one_start_runs_the_listen_and_the_speak_source(home_app):
    """#1001: the one Start = PC sound (listen leg) + microphone (speak leg)."""
    make, runs = home_app
    a = make()
    _pick_meeting(a)
    assert not hasattr(a, "quick_btns")
    a.both_btn.invoke()
    a.root.update()
    for _ in range(50):
        if runs:
            break
        a.root.after(20)
        a.root.update()
    assert runs and runs[0]["source"] == "system" and a.speak_run["source"] == "mic"


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


@pytest.mark.parametrize("mine,other,listen,speak", [
    ("zh-tw", "ja", ("ja", "zh-tw"), ("zh", "ja")),
    ("zh-tw", "ru", ("ru", "zh-tw"), ("zh", "ru")),
    ("en", "zh", ("zh", "en"), ("en", "zh-tw")),
    ("zh-tw", "auto", ("auto", "zh-tw"), ("zh", "en")),  # other side unknown: the #294 fallback
    ("zh-cn", "en", ("en", "zh-cn"), ("zh", "en")),  # #981: other side = my own language is refused (test_session_route_981)
])
def test_other_side_language_drives_listen_source_and_speak_target(mine, other, listen, speak):
    from tinglongwu.gui import core

    saved = dict(core.DEFAULTS, target=mine, lang=other)
    lis, spk = core.quick_settings(saved, "listen"), core.quick_settings(saved, "speak")
    assert (lis["lang"], lis["target"]) == listen and (spk["lang"], spk["target"]) == speak
    argv = core.settings_to_argv(spk)
    assert argv[argv.index("--lang") + 1] == speak[0]
    assert argv[argv.index("--to") + 1] == ("zh" if speak[1].startswith("zh") else speak[1])
    from tinglongwu import cli

    assert cli.parse_args(argv).to == argv[argv.index("--to") + 1]  # the CLI accepts the other side as --to


def test_home_picker_is_the_other_side_language_and_tour_step_1(home_app):
    from tinglongwu.langs import label

    make, runs = home_app
    a = make()
    _pick_meeting(a)
    assert str(a.home_lang.cget("textvariable")) == str(a.v_lang) == str(a.tour.lang_box.cget("textvariable"))
    a.home_lang.set(label("ja"))
    a.v_target.set("zh-tw")
    a.both_btn.invoke()
    a.root.update()
    for _ in range(50):
        if runs:
            break
        a.root.after(20)
        a.root.update()
    assert runs
    assert (runs[0]["lang"], runs[0]["target"]) == ("ja", "zh-tw")  # listen: other side -> mine
    assert (a.speak_run["lang"], a.speak_run["target"]) == ("zh", "ja")  # speak: mine -> other side
    a.stop_ev.set()
    if a.worker is not None:
        a.worker.join(2)
