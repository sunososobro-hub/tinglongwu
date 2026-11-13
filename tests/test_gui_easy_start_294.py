"""#294 easy start: first-screen "Listen to others" / "I speak" buttons, my-language only, Loading -> Ready."""
import pytest

from tinglongwu.gui import core


def test_listen_uses_pc_sound_and_my_language_without_touching_saved_settings():
    saved = dict(core.DEFAULTS, target="zh-cn", source="mic", lang="auto")
    run = core.quick_settings(saved, "listen")
    assert (run["source"], run["lang"], run["target"]) == ("system", "auto", "zh-cn")
    assert saved["source"] == "mic"  # the saved choice is not overwritten
    assert core.settings_to_argv(run)[:6] == ["--lang", "auto", "--to", "zh", "--zh-variant", "cn"]
    assert "--mic" not in core.settings_to_argv(run)


@pytest.mark.parametrize("mine,lang,target", [("zh-tw", "zh", "en"), ("zh-cn", "zh", "en"), ("en", "en", "zh-tw"),
                                              ("bogus", "zh", "en")])
def test_speak_uses_mic_from_my_language_to_the_other(mine, lang, target):
    run = core.quick_settings(dict(core.DEFAULTS, target=mine), "speak")
    assert (run["source"], run["lang"], run["target"]) == ("mic", lang, target)
    assert "--mic" in core.settings_to_argv(run)


def test_unknown_quick_mode_is_rejected():
    with pytest.raises(ValueError):
        core.quick_settings(dict(core.DEFAULTS), "sing")


def test_quick_button_states():
    assert core.quick_buttons("idle") == {"listen": ("gui_quick_listen", True), "speak": ("gui_quick_speak", True)}
    assert core.quick_buttons("loading", "listen") == {"listen": ("gui_loading", False), "speak": ("gui_loading", False)}
    assert core.quick_buttons("running", "speak") == {"listen": ("gui_quick_listen", False), "speak": ("gui_stop", True)}
    assert core.quick_buttons("busy", "speak") == {"listen": ("gui_quick_listen", False),
                                                   "speak": ("gui_quick_speak", False)}


@pytest.fixture
def gui_app(tmp_path, monkeypatch):
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
        runs.append(s)
        stop.wait(10)
        self.q.put(("stopped",))

    monkeypatch.setattr(app.App, "_work", fake_work)
    a = app.App()
    a.runs = runs
    a.root.update()
    yield a
    a.stop_ev.set()
    a.root.destroy()


def _btn(a, mode):
    b = a.quick_btns[mode]
    return b.cget("text"), str(b.cget("state"))


def test_one_click_listen_loading_then_ready_then_stop(gui_app):
    """Fresh profile: the only click is "Listen to others"; Loading... until models are ready, then a Ready hint."""
    from tinglongwu import cli

    a = gui_app
    assert _btn(a, "listen") == ("Listen to others", "normal") and _btn(a, "speak") == ("I speak", "normal")
    a.v_target.set("zh-tw")
    a.quick_btns["listen"].invoke()  # click 1
    assert _btn(a, "listen") == ("Loading... 0 s", "disabled") and _btn(a, "speak")[1] == "disabled"
    a.q.put(("status", f"{cli.READY_STATUS} - loaded in 2.0 s"))
    a.poll()
    assert a.v_status.get().startswith("Ready - start playback now")
    assert _btn(a, "listen") == ("Stop", "normal") and _btn(a, "speak") == ("I speak", "disabled")
    run = a.runs[0]
    assert (run["source"], run["target"]) == ("system", "zh-tw")
    a.quick_btns["listen"].invoke()  # Stop
    a.worker.join(5)
    a.poll()
    assert _btn(a, "listen") == ("Listen to others", "normal") and _btn(a, "speak") == ("I speak", "normal")


def test_speak_keeps_my_language_remembered(gui_app, tmp_path):
    from tinglongwu import cli

    a = gui_app
    a.v_target.set("en")
    a.toggle("speak")
    a.q.put(("status", cli.READY_STATUS))
    a.poll()
    assert a.v_status.get().startswith("Ready - speak now")
    assert (a.runs[0]["source"], a.runs[0]["lang"], a.runs[0]["target"]) == ("mic", "en", "zh-tw")
    assert core.load_settings(tmp_path / "tinglongwu" / "gui.json")["target"] == "en"  # my language, not zh-tw
