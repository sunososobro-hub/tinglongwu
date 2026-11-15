"""#894: the listen-only menus can pick the microphone (room sound) instead of system audio."""
import subprocess
import sys

import pytest

from tinglongwu import menuopts


@pytest.mark.parametrize("ans,out", [("y", ["--mic"]), ("YES", ["--mic"]), (" y ", ["--mic"]), ("", []), ("n", []), ("x", [])])
def test_mic_args(ans, out):
    assert menuopts.mic_args(ans) == out


def test_mic_args_cli():
    r = subprocess.run([sys.executable, "-m", "tinglongwu.menuopts", "mic-args", "y"], capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout.split() == ["--mic"]
    r = subprocess.run([sys.executable, "-m", "tinglongwu.menuopts", "mic-args", ""], capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout.strip() == ""


def test_every_locale_has_the_mic_question():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / "scripts" / "i18n"
    for loc in ("en", "ja", "ru", "zh-TW"):
        assert "M_ASK_MIC=" in (root / f"{loc}.sh").read_text(encoding="utf-8")
        assert "ASK_MIC =" in (root / f"{loc}.psd1").read_text(encoding="utf-8")
