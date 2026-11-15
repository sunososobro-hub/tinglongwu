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


def test_lang_fields_exactly_two_labels_no_source_or_translate_to(app):
    from tinglongwu.i18n import t
    texts = _texts(app)
    assert t("gui_quick_target") in texts and t("gui_quick_other") in texts
    assert t("gui_src_lang") not in texts and t("gui_target") not in texts
    langs = [w for w in _walk(app.panel) if w.winfo_class() == "TCombobox" and w in (app.home_lang, app.home_target)]
    assert len(langs) == 2


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
