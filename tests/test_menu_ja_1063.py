"""#1063: Japanese text menus (bash + Windows): key parity with en, ja locale normalisation, Japanese output."""
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
COMMON = SCRIPTS / "tlwu_common.sh"
I18N = SCRIPTS / "i18n"
JA_CHARS = re.compile(r"[぀-ヿ一-鿿]")


def _sh_keys(name):
    return set(re.findall(r"(?:^|;)\s*(M_\w+)=", (I18N / name).read_text(encoding="utf-8"), re.M))


def _psd1_keys(name):
    return set(re.findall(r"(?:^|;)\s*(\w+) = '", (I18N / name).read_text(encoding="utf-8-sig"), re.M))


def _bash(snippet, **env):
    r = subprocess.run(["bash", "-c", f". '{COMMON}'; {snippet}"], capture_output=True, text=True,
                       encoding="utf-8", env={**os.environ, **env})
    return r.stdout.strip()


def test_ja_sh_has_every_en_key():
    assert _sh_keys("en.sh") - _sh_keys("ja.sh") == set()


def test_ja_psd1_has_every_en_key():
    assert _psd1_keys("en.psd1") - _psd1_keys("ja.psd1") == set()


def test_psd1_has_bom():
    assert (I18N / "ja.psd1").read_bytes()[:3] == b"\xef\xbb\xbf"


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
@pytest.mark.parametrize("tag", ["ja_JP.UTF-8", "ja", "ja-JP"])
def test_norm_lang_ja(tag):
    assert _bash(f"tlwu_norm_lang {tag}") == "ja"


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_norm_lang_others_unchanged():
    assert _bash("tlwu_norm_lang ru_RU.UTF-8") == "ru"
    assert _bash("tlwu_norm_lang en_US.UTF-8") == "en"


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_pick_ui_lang_accepts_ja(tmp_path):
    out = _bash("echo ja | tlwu_pick_ui_lang >/dev/null; tlwu_cfg_get ui_lang", TLWU_CONFIG_DIR=str(tmp_path),
                TLWU_UI_LANG="")
    assert out == "ja"


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_menu_prints_japanese_when_ui_lang_ja():
    out = _bash('tlwu_load_i18n; m M_HEAD auto; m M_OPT_LISTEN en', TLWU_UI_LANG="ja")
    assert JA_CHARS.search(out), out
    assert "相手の言語" in out
