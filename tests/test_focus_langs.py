"""#58: focus languages ja / en / ru are listed first in every language menu; the rest stay selectable but are
labeled experimental. Nothing is removed. All checks run the real code (Python module, bash menu, GUI)."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tinglongwu import langs
from tinglongwu.gui import core

ROOT = Path(__file__).resolve().parent.parent
# the GUI list before #58: no code may disappear
OLD_GUI = {"auto", "ru", "ja", "en", "ko", "zh", "de", "fr", "es", "it", "pt", "vi", "th", "id"}
OLD_MENU_CODES = ("ko", "de", "fr", "es")  # what the menu L help listed besides auto/en/ru/ja


def _env(ui):
    return dict(os.environ, TLWU_UI_LANG=ui, PYTHONPATH=str(ROOT), PYTHONIOENCODING="utf-8")


def test_gui_order_focus_first_nothing_removed():
    assert set(core.LANGS) == OLD_GUI
    assert core.LANGS[:4] == ["auto", "ja", "en", "ru"]
    last_focus = max(core.LANGS.index(c) for c in ("ja", "en", "ru"))
    assert all(core.LANGS.index(c) > last_focus for c in OLD_GUI - {"auto", "ja", "en", "ru"})


@pytest.mark.parametrize("ui,word", [("en", "experimental"), ("ru", "экспериментальный"), ("zh-TW", "實驗性")])
def test_gui_labels_mark_only_non_focus(ui, word):
    labels = langs.labels(ui)
    by_code = {langs.from_label(lab): lab for lab in labels}
    assert list(by_code) == core.LANGS  # every label maps back to its code, same order
    for c in ("auto", "ja", "en", "ru", "zh"):  # #884: names, never raw codes
        assert by_code[c] == langs.plain_label(c, ui) and by_code[c] != c
    for c in OLD_GUI - {"auto", "ja", "en", "ru", "zh"}:
        assert by_code[c] == f"{langs.plain_label(c, ui)} ({word})"
    assert langs.from_label("ru") == "ru" and langs.from_label("") == "auto"


def _help(ui):
    r = subprocess.run([sys.executable, "-m", "tinglongwu.langs"], capture_output=True, env=_env(ui), cwd=ROOT)
    assert r.returncode == 0, r.stderr
    return r.stdout.decode("utf-8")


@pytest.mark.parametrize("ui,head,names", [
    ("en", "Experimental", ("ja Japanese", "en English", "ru Russian")),
    ("ru", "Экспериментальные", ("ja японский", "en английский", "ru русский")),
    ("zh-TW", "實驗性", ("ja 日文", "en 英文", "ru 俄文")),
])
def test_menu_help_lists_focus_then_experimental(ui, head, names):
    out = _help(ui)
    cut = out.index(head)
    pos = [out.index(n) for n in names]
    assert pos == sorted(pos) and pos[-1] < cut  # ja, en, ru in that order, before the experimental block
    for c in OLD_MENU_CODES:
        assert f"{c} " in out[cut:], (ui, c)  # still listed (selectable), under the experimental heading


@pytest.mark.parametrize("code,want", [("ko", "Korean (experimental)"), ("es", "Spanish (experimental)"),
                                       ("ja", "Japanese"), ("ru", "Russian"), ("xx", "xx")])
def test_menu_header_name(code, want):
    r = subprocess.run([sys.executable, "-m", "tinglongwu.langs", "--name", code], capture_output=True,
                       env=_env("en"), cwd=ROOT)
    assert r.stdout.decode("utf-8").strip() == want


def _run_bash_menu(tmp_path, keys, ui="en"):
    """The real scripts/linux_menu.sh (Linux + macOS tlwu-menu) in a scratch copy with a stub .venv."""
    shutil.copytree(ROOT / "scripts", tmp_path / "scripts")
    venv = tmp_path / ".venv" / "bin"
    venv.mkdir(parents=True)
    (venv / "activate").write_text(f'export PATH="{Path(sys.executable).parent}:$PATH"\n'
                                   f'export PYTHONPATH="{ROOT}"\n', "utf-8")
    env = dict(os.environ, TLWU_UI_LANG=ui, TLWU_NO_UPDATE="1", TLWU_CONFIG_DIR=str(tmp_path / "cfg"),
               HOME=str(tmp_path), XDG_RUNTIME_DIR=str(tmp_path))
    env.pop("TLWU_LANG", None)
    r = subprocess.run(["bash", str(tmp_path / "scripts" / "linux_menu.sh")], input=keys.encode(),
                       capture_output=True, env=env, timeout=120)
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    return r.stdout.decode("utf-8")


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_bash_menu_language_list_and_experimental_header(tmp_path):
    out = _run_bash_menu(tmp_path, "l\nko\nl\nja\n")
    cut = out.index("Experimental")
    assert out.index("ja Japanese") < out.index("en English") < out.index("ru Russian") < cut
    assert "ko Korean" in out[cut:]
    assert "other side speaks: Auto (JA/EN/RU)" in out
    assert "other side speaks: Korean (experimental)" in out
    assert "other side speaks: Japanese    " in out


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="needs PowerShell (present on CI ubuntu runners)")
def test_windows_menu_lang_name_marks_experimental():
    """Get-LangName from scripts/windows_menu.ps1, extracted and run with a stub $py (the real python)."""
    src = (ROOT / "scripts" / "windows_menu.ps1").read_text("utf-8-sig")
    start = src.index("function Get-LangName")
    end = src.index("\n}\n", start) + 3
    script = (f"$py = '{sys.executable}'; $script:M = @{{ LN_auto = 'Auto (JA/EN/RU)' }}\n" + src[start:end] +
              "\nWrite-Output (Get-LangName 'ko'); Write-Output (Get-LangName 'auto'); Write-Output (Get-LangName 'ja')")
    r = subprocess.run(["pwsh", "-NoProfile", "-Command", script], capture_output=True, env=_env("en"), cwd=ROOT)
    assert r.stdout.decode("utf-8").split() == ["Korean", "(experimental)", "Auto", "(JA/EN/RU)", "Japanese"]


def test_gui_saves_code_not_label(tmp_path, monkeypatch):
    """GUI stores the plain code even when the dropdown shows 'ko (experimental)'."""
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
    a = app.App()
    try:
        values = list(a.combos["lang"].cget("values"))
        assert values[:4] == ["Auto", "Japanese", "English", "Russian"] and "Korean (experimental)" in values
        a.v_lang.set("Korean (experimental)")
        i18n.set_ui_lang("ru", persist=False)
        a.relabel()
        assert a.v_lang.get() == "корейский (экспериментальный)"
        a.collect()
        assert a.settings["lang"] == "ko" and core.load_settings()["lang"] == "ko"
    finally:
        a.root.destroy()
