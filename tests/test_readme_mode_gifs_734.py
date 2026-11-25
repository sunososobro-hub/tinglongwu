"""#734: every README's first screen shows the three per-mode GIFs (with alt text) and each GIF is within its cap."""
import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NAMES = ("README.md", "docs/README.zh-TW.md", "docs/README.zh-CN.md")
GIFS = ("mode_listen.gif", "mode_speak.gif", "mode_two_way.gif")

_spec = importlib.util.spec_from_file_location("readme_gif", ROOT / "scripts" / "readme_gif.py")
rg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rg)


@pytest.mark.parametrize("name", NAMES)
def test_first_screen_has_each_mode_gif_with_alt(name):
    first = (ROOT / name).read_text("utf-8").split("<details>", 1)[0]
    for gif in GIFS:
        m = re.search(r'<img src="((?:\.\./)?assets/demo/' + re.escape(gif) + r')" alt="([^"]+)"', first)
        assert m, (name, gif)
        assert (ROOT / name).parent.joinpath(m.group(1)).resolve().is_file(), (name, gif)


def test_mode_gifs_within_caps():
    assert rg.main(["check", "--dir", str(ROOT / "assets" / "demo"), "--present-only"]) == 0
    for gif in GIFS:
        assert (ROOT / "assets" / "demo" / gif).is_file(), gif
