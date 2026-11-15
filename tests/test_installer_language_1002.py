"""#1002: the wizard asks for the language first; every {cm:...} text exists in every [Languages] entry."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "installer"))
import build_installer  # noqa: E402


def _iss() -> str:
    return build_installer.preprocess((ROOT / "installer" / "tinglongwu.iss").read_text(encoding="utf-8-sig"), set())


def _section(text: str, name: str) -> list[str]:
    m = re.search(rf"^\[{name}\]\n(.*?)(?=^\[|\Z)", text.replace("\r\n", "\n"), re.S | re.M)
    return [ln for ln in (m.group(1).splitlines() if m else []) if ln.strip() and not ln.lstrip().startswith(";")]


def test_language_dialog_and_languages():
    text = _iss()
    assert re.search(r"^ShowLanguageDialog=yes", text, re.M)
    langs = [re.match(r'Name: "(\w+)"', ln).group(1) for ln in _section(text, "Languages")]
    assert {"english", "japanese"} <= set(langs)


def test_every_custom_message_exists_in_every_language():
    text = _iss()
    langs = [re.match(r'Name: "(\w+)"', ln).group(1) for ln in _section(text, "Languages")]
    defined = {ln.split("=", 1)[0] for ln in _section(text, "CustomMessages")}
    used = set(re.findall(r"\{cm:(\w+)\}", text))
    assert used
    for lang in langs:
        for key in used:
            assert f"{lang}.{key}" in defined, f"{lang}.{key} missing"


def test_no_mixed_language_task_text():
    for ln in _section(_iss(), "Tasks"):
        assert " / " not in ln.split("Description:", 1)[1].split(";")[0]
