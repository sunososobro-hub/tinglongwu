"""#1820: GUI colour tokens live in tinglongwu/gui/theme.py only."""
import pathlib
import re

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
