"""#1820: GUI colour tokens live in tinglongwu/gui/theme.py only."""
import pathlib
import re

import pytest

from tinglongwu.gui import theme

GUI = pathlib.Path(theme.__file__).parent


def _lum(h):
    c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    c = [v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def ratio(a, b):
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def test_themes_same_token_names():
    assert set(theme.TOKENS["dark"]) == set(theme.TOKENS["light"])
    for name in ("bg", "surface", "text", "text_muted", "accent", "warning", "error", "success", "grip", "grip_hover"):
        assert name in theme.TOKENS["dark"]


def test_text_background_contrast():
    for mode, t in theme.TOKENS.items():
        for fg in ("text", "text_muted", "warning", "error"):
            for bg in ("bg", "surface"):
                assert ratio(t[fg], t[bg]) >= 4.5, (mode, fg, bg, ratio(t[fg], t[bg]))


def test_pal_derives_from_tokens():
    for mode, t in theme.TOKENS.items():
        assert theme.PAL[mode]["warn"] == t["warning"] and theme.PAL[mode]["err"] == t["error"]


def test_no_raw_hex_outside_theme_and_speaker_palette():
    pat = re.compile(r"[\"']#[0-9a-fA-F]{3,6}[\"']")
    bad = []
    for f in GUI.glob("*.py"):
        if f.name == "theme.py":
            continue
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if pat.search(line) and not (f.name == "core.py" and ("SPK_PALETTE" in line or "SPK_AVATAR_FG" in line)):
                bad.append(f"{f.name}:{i}")
    assert not bad, bad


# ---- #1821: type scale + spacing scale ----

def test_type_scale_roles_and_spacing_scale():
    assert list(theme.TYPE) == ["caption", "body", "title", "heading", "display"]
    sizes = list(theme.TYPE.values())
    assert sizes == sorted(sizes) and len(set(sizes)) == 5
    assert list(theme.SPACE.values()) == [4, 8, 12, 16, 24]


def test_font_helper_returns_tk_tuples():
    assert theme.font("body") == (theme.FONT_FAMILY, theme.TYPE["body"])
    assert theme.font("heading", bold=True, family="Arial") == ("Arial", theme.TYPE["heading"], "bold")


def test_small_size_follows_subtitle_size_with_floor():
    assert theme.small_size(48) == 32 and theme.small_size(24) == 16 and theme.small_size(9) == 8
    assert theme.small_size(12, 9) == 9 and theme.bump(30) == 31


def test_app_has_no_tkdefaultfont_literal_or_numeric_font_size():
    src = (GUI / "app.py").read_text(encoding="utf-8")
    assert "TkDefaultFont" not in src
    numeric = re.findall(r"font=\(\s*[\w.]+\s*,\s*\d", src) + re.findall(r"\bsize=\d", src)
    assert not numeric, numeric


def test_subtitle_size_setting_changes_rendered_font_size(monkeypatch):
    pytest.importorskip("tkinter")
    from tinglongwu.gui import app as gui_app

    made, tags = [], {}

    class FakeFont:
        def __init__(self, **kw):
            made.append(kw)

    class FakeText:
        def tag_configure(self, tag, **kw):
            tags[tag] = kw

    class FakeOverlay:
        rows = 3

        def __init__(self):
            self.app = type("A", (), {"settings": {}})()
            self.text = FakeText()
            self.set_rows = lambda r: None

    monkeypatch.setattr(gui_app.tkfont, "Font", FakeFont)
    for n, small in ((20, 13), (48, 32)):
        ov = FakeOverlay()
        gui_app.Overlay.set_font(ov, n)
        assert made[-1]["size"] == n and ov.app.settings["font_size"] == n
        assert tags["src"]["font"][1] == small
