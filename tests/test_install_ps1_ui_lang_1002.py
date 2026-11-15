"""#1002: install.ps1 -Lang maps the wizard language to the same ui_lang as tinglongwu.i18n.normalize."""
import re
from pathlib import Path

from tinglongwu import i18n

ROOT = Path(__file__).resolve().parents[1]


def _ps1_ui_lang(lang: str) -> str:
    text = (ROOT / "install.ps1").read_text("utf-8-sig")
    line = re.search(r"\$ui = if (.+)", text).group(1)
    for pat, val in re.findall(r"\$Lang -like '([^']+)'\) \{ '([\w-]+)' \}", line):
        if re.fullmatch(pat.replace("*", ".*"), lang):
            return val
    return re.search(r"else \{ '([\w-]+)' \}", line).group(1)


def test_install_ps1_ui_lang_matches_i18n():
    for code in ("en", "zh-TW", "zh-CN", "ja", "ru"):
        assert _ps1_ui_lang(code) == i18n.normalize(code), code
