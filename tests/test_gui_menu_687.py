"""#738 (#687 part 2): File / Settings / Help menu bar holds the rarely used actions, in en / ru / zh-TW."""
from __future__ import annotations

import pytest

from tests.test_gui import _bound_vars, _wizard_install, gui_app  # noqa: F401 - fixtures

UI = {"English": "en", "Русский": "ru", "繁體中文": "zh-TW"}


def _entries(menu):
    """[(label, type, index)] of a tk.Menu (separators and tear-offs left out)."""
    end = menu.index("end")
    out = []
    for i in range(0 if end is None else end + 1):
        kind = menu.type(i)
        if kind in ("separator", "tearoff"):
            continue
        out.append((menu.entrycget(i, "label"), kind, i))
    return out


def _walk(menu, path=()):
    """Every leaf item of the menu tree: (path of cascade labels, label, menu, index)."""
    for label, kind, i in _entries(menu):
        if kind == "cascade":
            sub = menu.nametowidget(menu.entrycget(i, "menu"))
            yield (*path, label), label, menu, i
            yield from _walk(sub, (*path, label))
        else:
            yield (*path, label), label, menu, i


def _find(a, *path):
    for p, _label, menu, i in _walk(a.menubar):
        if p == path:
            return menu, i
    raise AssertionError(f"no menu item {path}: {[p for p, *_ in _walk(a.menubar)]}")


def _switch(a, ui):
    a.v_ui_lang.set(ui)
    a.switch_lang()
    a.root.update_idletasks()


def _labels(t):
    return {"file": t("gui_menu_file"), "settings": t("gui_menu_settings"), "help": t("gui_menu_help")}


@pytest.mark.parametrize("ui", list(UI))
def test_every_listed_item_is_in_the_menu_bar_exactly_once(_wizard_install, gui_app, ui):  # noqa: F811
    from tinglongwu.gui import app as app_mod
    from tinglongwu.i18n import t

    a = gui_app
    _switch(a, ui)
    assert str(a.root.cget("menu")) == str(a.menubar)
    m = _labels(t)
    assert [label for label, _k, _i in _entries(a.menubar)] == [m["file"], m["settings"], m["help"]]
    want = [(m["file"], t("gui_menu_logs")), (m["file"], t("gui_menu_uninstall")), (m["file"], t("gui_menu_quit")),
            (m["settings"], t("gui_ui_lang")), (m["settings"], t("gui_lines")), (m["settings"], t("gui_font_size")),
            (m["help"], t("gui_menu_tour")), (m["help"], t("gui_upd_check")), (m["help"], t("gui_upd_auto")),
            (m["help"], t("gui_report")), (m["help"], t("gui_menu_about"))]
    if app_mod.sv_ttk is not None:
        want.append((m["settings"], t("gui_dark")))
    paths = [p for p, *_ in _walk(a.menubar)]
    for item in want:
        assert paths.count(item) == 1, (ui, item)
    for name in UI:  # the language list is in each language's own name
        assert paths.count((m["settings"], t("gui_ui_lang"), name)) == 1


def test_none_of_the_menu_items_remains_on_the_bar_or_the_panel(_wizard_install, gui_app):  # noqa: F811
    from tinglongwu import __version__
    from tinglongwu.i18n import t

    a = gui_app
    moved = {t(k) for k in ("gui_report", "gui_upd_check", "gui_upd_auto", "gui_ui_lang", "gui_dark", "gui_lines",
                            "gui_font_size", "gui_menu_tour", "gui_menu_logs", "gui_menu_uninstall",
                            "gui_menu_about")} | {"?", f"v{__version__}"}
    for top in (a.root, a.panel):
        stack, texts = [top], []
        while stack:
            w = stack.pop()
            stack.extend(c for c in w.winfo_children() if not str(c).startswith(str(a.menubar)))
            if w is not top and "text" in w.keys():
                texts.append(w.cget("text"))
        assert moved.isdisjoint(texts), (top, moved & set(texts))
    in_windows = _bound_vars(a.panel) | _bound_vars(a.bar)
    for v in (a.v_ui_lang, a.v_lines, a.v_font, a.v_auto_upd):
        assert str(v) not in in_windows


def test_menu_commands_work_with_fakes(_wizard_install, gui_app, monkeypatch):  # noqa: F811
    from tinglongwu import __version__
    from tinglongwu.gui import app as app_mod
    from tinglongwu.i18n import t

    a = gui_app
    m = _labels(t)
    opened, shown, checked, reported = [], [], [], []
    monkeypatch.setattr(app_mod, "open_target", opened.append)
    monkeypatch.setattr(app_mod.messagebox, "showinfo", lambda title, text, **k: shown.append((title, text)))
    monkeypatch.setattr(app_mod.App, "_check_update", lambda self, manual: checked.append(manual))
    monkeypatch.setattr(app_mod.App, "report_problem", lambda self: reported.append(True))
    monkeypatch.setattr(app_mod.sys, "platform", "win32")

    menu, i = _find(a, m["file"], t("gui_menu_uninstall"))
    menu.invoke(i)
    assert opened == ["ms-settings:appsfeatures"]
    menu, i = _find(a, m["file"], t("gui_menu_logs"))
    menu.invoke(i)
    assert opened[-1] == str(app_mod.userdirs.log_dir())
    menu, i = _find(a, m["help"], t("gui_menu_about"))
    menu.invoke(i)
    assert shown and __version__ in shown[-1][1]
    menu, i = _find(a, m["help"], t("gui_upd_check"))
    menu.invoke(i)
    assert checked == [True]
    menu, i = _find(a, m["help"], t("gui_report"))
    menu.invoke(i)
    assert reported == [True]
    menu, i = _find(a, m["help"], t("gui_menu_tour"))
    menu.invoke(i)
    a.root.update()
    assert a.tour.visible

    menu, i = _find(a, m["settings"], t("gui_lines"), "1")
    menu.invoke(i)
    assert a.settings["overlay_lines"] == 1 and a.overlay.rows == 1
    menu, i = _find(a, m["settings"], t("gui_font_size"), "40")
    menu.invoke(i)
    assert a.settings["font_size"] == 40
    menu, i = _find(a, m["settings"], t("gui_ui_lang"), "Русский")
    menu.invoke(i)
    assert a.btn.cget("text") == t("gui_start") and t("gui_menu_file") == "Файл"
    assert _entries(a.menubar)[0][0] == "Файл"  # the menu bar follows the language switch


def test_uninstall_off_windows_explains_instead_of_opening(gui_app, monkeypatch):  # noqa: F811
    from tinglongwu.gui import app as app_mod
    from tinglongwu.i18n import t

    a = gui_app
    opened, shown = [], []
    monkeypatch.setattr(app_mod, "open_target", opened.append)
    monkeypatch.setattr(app_mod.messagebox, "showinfo", lambda title, text, **k: shown.append(text))
    monkeypatch.setattr(app_mod.sys, "platform", "linux")
    a.open_uninstall()
    assert opened == [] and shown == [t("gui_uninstall_other")]


def test_no_update_items_on_a_git_checkout(gui_app):  # noqa: F811
    from tinglongwu.i18n import t

    labels = [label for _p, label, *_ in _walk(gui_app.menubar)]
    assert t("gui_upd_check") not in labels and t("gui_upd_auto") not in labels
    assert t("gui_report") in labels
