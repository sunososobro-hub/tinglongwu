"""#1002: the wizard language (install.ps1 -Lang) becomes the app UI language.

Runs the real `ui_lang` block of install.ps1 in pwsh (preinstalled on the GitHub ubuntu runners; skipped where
missing) against a temp TLWU_CONFIG_DIR and checks the written `ui_lang=` equals tinglongwu.i18n.normalize(lang),
so 日本語 opens a Japanese app instead of an English one."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tinglongwu import i18n

ROOT = Path(__file__).resolve().parents[1]
PWSH = shutil.which("pwsh")
needs_pwsh = pytest.mark.skipif(PWSH is None, reason="pwsh not installed")


def _ui_lang_block() -> str:
    text = (ROOT / "install.ps1").read_text("utf-8-sig")
    m = re.search(r"^if \(\$Lang -and -not \$DryRun\) \{\n.*?^\}\n", text, re.S | re.M)
    assert m, "ui_lang block not found in install.ps1"
    return m.group(0)


def _run_block(lang: str, cfg_dir: Path) -> list[str]:
    script = f"$Lang = '{lang}'; $DryRun = $false\n{_ui_lang_block()}"
    env = {**os.environ, "TLWU_CONFIG_DIR": str(cfg_dir), "POWERSHELL_TELEMETRY_OPTOUT": "1",
           "POWERSHELL_UPDATECHECK": "Off"}
    r = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True,
                       text=True, encoding="utf-8", timeout=120, env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    return (cfg_dir / "config").read_text("ascii").splitlines()


@needs_pwsh
@pytest.mark.parametrize("lang", ["en", "zh-TW", "zh-CN", "ja", "ja-JP", "ru"])
def test_wizard_lang_writes_i18n_ui_lang(lang, tmp_path):
    (tmp_path / "config").write_text("theme=dark\nui_lang=xx\n", "ascii")
    lines = _run_block(lang, tmp_path)
    assert f"ui_lang={i18n.normalize(lang)}" in lines, lines
    assert [x for x in lines if x.startswith("ui_lang=")] == [f"ui_lang={i18n.normalize(lang)}"]
    assert "theme=dark" in lines
