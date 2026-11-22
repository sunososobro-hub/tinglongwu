"""#1002: the wizard asks for the language first and every page is then in that language only.

Inno Pascal cannot run on Linux (the compiled wizard runs in windows-build), so this checks the .iss through
build_installer's preprocess / message_problems (the same check the build runs before ISCC) and renders the
[CustomMessages] the wizard shows."""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "release" / "installer"))
import build_installer  # noqa: E402

from tinglongwu import i18n  # noqa: E402

RAW = (ROOT / "release" / "installer" / "tinglongwu.iss").read_text(encoding="utf-8-sig")
LAYOUTS = {"source": set(), "core": {"CoreLayout", "DefaultListenMB"}}
CJK = re.compile(r"[぀-ヿ㐀-鿿가-힯＀-￯]")
CYRILLIC = re.compile(r"[Ѐ-ӿ]")


def _iss(layout: str = "source") -> str:
    return build_installer.preprocess(RAW, LAYOUTS[layout])


def _code(layout: str = "source") -> str:
    code = _iss(layout).split("[Code]", 1)[1]
    return re.sub(r"\{[^}]*\}", "", code)  # drop Pascal { comments } so only code is checked


def test_language_dialog_lists_english_both_chinese_and_japanese():
    text = _iss()
    assert re.search(r"^ShowLanguageDialog=yes", text, re.M)
    assert {"english", "chinesetrad", "chinesesimp", "japanese"} <= set(build_installer.wizard_languages(text))


def test_traditional_chinese_comes_before_simplified():
    """zh-HK / zh-MO / zh-SG Windows have no exact match: Inno takes the first entry of the primary language."""
    langs = list(build_installer.wizard_languages(_iss()))
    assert langs.index("chinesetrad") < langs.index("chinesesimp")


@pytest.mark.parametrize("layout", LAYOUTS)
def test_every_language_has_every_message_and_its_messages_file(layout):
    assert build_installer.message_problems(_iss(layout)) == []


def test_problems_are_reported(tmp_path):
    """A dropped message, a missing vendored .isl and one without a BOM each fail the build check."""
    langs = tmp_path / "Languages"
    langs.mkdir()
    (langs / "X.isl").write_bytes(b"[LangOptions]\n")  # no BOM
    text = ('[Languages]\nName: "english"; MessagesFile: "compiler:Default.isl"\n'
            'Name: "xx"; MessagesFile: "compiler:Default.isl,Languages\\X.isl"\n'
            'Name: "yy"; MessagesFile: "Languages\\Y.isl"\n\n'
            "[CustomMessages]\nenglish.A=a %1\nenglish.B=b\nxx.A=a\nyy.A=a %1\nyy.B=b\n\n"
            "[Code]\nbegin FmtMessage(CustomMessage('A'), ['1']); CustomMessage('B'); end;\n")
    got = build_installer.message_problems(text, tmp_path)
    assert "xx: messages file Languages\\X.isl has no UTF-8 BOM" in got
    assert "yy: messages file Languages\\Y.isl not found" in got
    assert "xx.B missing" in got and "xx.A uses other %N arguments than the first language" in got
    assert not any(p.startswith("english") for p in got)


def test_vendored_chinese_messages_files_are_chinese():
    for name, lang_id in (("ChineseTraditional", "$0404"), ("ChineseSimplified", "$0804")):
        isl = (ROOT / "release" / "installer" / "Languages" / f"{name}.isl").read_text("utf-8-sig")
        assert f"LanguageID={lang_id}" in isl
        assert CJK.search(isl.split("[Messages]", 1)[1])


@pytest.mark.parametrize("layout", LAYOUTS)
def test_code_shows_no_hard_coded_language_text(layout):
    """Every user text in [Code] comes from [CustomMessages]: no Chinese / Japanese / Russian literal, no "x / y" pair."""
    for lit in re.findall(r"'((?:[^']|'')*)'", _code(layout)):
        assert not CJK.search(lit) and not CYRILLIC.search(lit), lit
        assert " / " not in lit, lit


def test_each_language_is_single_language():
    text = _iss()
    for lang in build_installer.wizard_languages(text):
        for key, msg in build_installer.custom_messages(text, lang).items():
            msg = re.sub(r"\{#\w+\}", "", msg)  # the brand name "聽攏有 tinglongWu" is the same everywhere
            assert " / " not in msg, (lang, key)
            if lang in ("english", "russian"):
                assert not CJK.search(msg), (lang, key)
            if lang != "russian":
                assert not CYRILLIC.search(msg), (lang, key)
            if lang in ("chinesetrad", "chinesesimp", "japanese"):
                assert CJK.search(msg) or key in ("RunLog",) or msg.strip().startswith("%"), (lang, key)


def _wizard_lang_codes() -> dict[str, str]:
    body = re.search(r"function WizardLangCode: String;\s*begin(.*?)\nend;", _code(), re.S).group(1)
    codes = dict(re.findall(r"ActiveLanguage = '(\w+)' then Result := '([\w-]+)'", body))
    default = re.search(r"Result := '([\w-]+)';\n", body).group(1)
    return {lang: codes.get(lang, default) for lang in build_installer.wizard_languages(_iss())}


def test_wizard_language_becomes_the_app_language():
    codes = _wizard_lang_codes()
    assert codes == {"english": "en", "chinesetrad": "zh-TW", "chinesesimp": "zh-CN", "japanese": "ja", "russian": "ru"}
    ui = {lang: i18n.normalize(code) for lang, code in codes.items()}
    assert ui == {"english": "en", "chinesetrad": "zh-TW", "chinesesimp": "zh-TW", "japanese": "ja", "russian": "ru"}
    # run_install.ps1 passes -Lang on only when it matches this pattern
    pattern = re.search(r"\$Lang -match '([^']+)'", (ROOT / "release" / "installer" / "run_install.ps1").read_text("utf-8-sig"))
    assert all(re.fullmatch(pattern.group(1), c) for c in codes.values())


def test_lang_and_ui_lang_always_follow_the_wizard_but_not_a_silent_update():
    code = _code()
    # install.ps1 -Lang rewrites ui_lang too (CodeRabbit on #1056): both writes are skipped on a silent self-update
    assert "if not WizardSilent then Params := Params + ' -Lang ' + WizardLangCode;" in code
    assert code.count("' -Lang '") == 1
    assert "if not WizardSilent then SetConfigValue('ui_lang', WizardLangCode);" in code
    assert code.count("SetConfigValue('ui_lang'") == 1


def test_no_code_line_starts_with_a_section_bracket():
    """ISCC 6.7.1 in windows-build: "Error on line 607 ... Invalid section tag" for a wrapped `[args]` line."""
    assert build_installer.section_tag_lines("[Setup]\n[Code]\nbegin\n  x := F(a,\n    [1]);\nend;\n") == [5]
    assert build_installer.section_tag_lines(RAW) == []
