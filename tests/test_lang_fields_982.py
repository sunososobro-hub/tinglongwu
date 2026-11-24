"""#982: the GUI has exactly two language fields (My language, Other side language); same language disables Start."""
import pytest


def _walk(w):
    yield w
    for c in w.winfo_children():
        yield from _walk(c)


@pytest.fixture
def app(tmp_path, monkeypatch):
    tk = pytest.importorskip("tkinter")
    try:
        tk.Tk().destroy()
    except tk.TclError:
        pytest.skip("no display")
    from tinglongwu import i18n
    from tinglongwu.gui import app as appmod

    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(i18n, "_system_tag", lambda: "en-US")
    monkeypatch.setattr(i18n.userdirs, "write_config_value", lambda k, v: None)
    monkeypatch.setattr(i18n, "_override", "en")
    a = appmod.App()
    yield a
    a.root.destroy()


def _texts(a):
    out = []
    for w in _walk(a.panel):
        try:
            out.append(str(w.cget("text")))
        except Exception:
            pass
    return out


def test_lang_fields_exactly_two_visible_language_fields(app):
    app.panel.deiconify()
    app.root.update()
    assert app.btn_pair.winfo_ismapped() and not app.home_lang.winfo_ismapped()  # #1603: one pair button
    # the Advanced 來源語言 / 翻譯成 rows are not on screen (even with Advanced open)
    app.set_advanced(True)
    app.root.update()
    assert not app.combos["lang"].winfo_ismapped() and not app.combos["target"].winfo_ismapped()


def test_lang_fields_same_language_shows_error_and_disables_start(app):
    from tinglongwu.i18n import t
    app.v_target.set("en")
    app.v_lang.set("English")
    app.root.update_idletasks()
    assert app.lang_err.cget("text") == t("gui_same_lang")
    assert str(app.both_btn.cget("state")) == "disabled" and str(app.btn.cget("state")) == "disabled"
    app.v_lang.set("Japanese")
    app.root.update_idletasks()
    assert app.lang_err.cget("text") == "" and str(app.both_btn.cget("state")) == "normal"


def test_lang_fields_changing_one_never_rewrites_the_other(app):
    app.v_target.set("en")
    app.v_lang.set("Japanese")
    assert app.v_target.get() == "en"
    app.v_target.set("zh-tw")
    assert app.v_lang.get() == "Japanese"


def test_lang_fields_saved_matching_languages_start_disabled(tmp_path, monkeypatch):
    tk = pytest.importorskip("tkinter")
    try:
        tk.Tk().destroy()
    except tk.TclError:
        pytest.skip("no display")
    from tinglongwu import i18n
    from tinglongwu.gui import app as appmod, core

    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(i18n, "_system_tag", lambda: "en-US")
    monkeypatch.setattr(i18n.userdirs, "write_config_value", lambda k, v: None)
    monkeypatch.setattr(i18n, "_override", "en")
    real = core.load_settings

    def same(*a, **k):
        s = real(*a, **k)
        s.update(lang="en", target="en")
        return s

    monkeypatch.setattr(appmod, "load_settings", same, raising=False)
    a = appmod.App()
    try:
        assert str(a.both_btn.cget("state")) == "disabled" and str(a.btn.cget("state")) == "disabled"
    finally:
        a.root.destroy()
