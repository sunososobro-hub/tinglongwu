"""#11: scripts/bootstrap_windows.ps1 must be pure ASCII (PS 5.1 reads BOM-less files as ANSI and a BOM is
unsafe through `irm | iex`), with non-English messages stored as base64 UTF-8 and decoded at runtime."""
import base64
import re
import shutil
import subprocess
from pathlib import Path

import pytest

PS1 = Path(__file__).resolve().parents[1] / "scripts" / "bootstrap_windows.ps1"
KEYS = {"start", "git", "zip", "fail", "dirty", "run"}


def _messages():
    """Decode the $Msgs table the same way the script does ($u = UTF8.GetString(FromBase64String(...)))."""
    text = PS1.read_text(encoding="ascii")
    en_line = re.search(r"'en' = @\{ (.*) \}", text).group(1)
    msgs = {"en": {k: v.replace("''", "'") for k, v in re.findall(r"(\w+) = '((?:[^']|'')*)'", en_line)}}
    for lang in ("ru", "zh-TW"):
        block = re.search(r"'%s' = @\{\n(.*?)\n\s*\}" % re.escape(lang), text, re.S).group(1)
        pairs = re.findall(r"(\w+) = \(& \$u '([A-Za-z0-9+/=]+)'\)", block)
        msgs[lang] = {k: base64.b64decode(b, validate=True).decode("utf-8") for k, b in pairs}
    return msgs


def test_file_is_pure_ascii_without_bom():
    data = PS1.read_bytes()
    assert not data.startswith(b"\xef\xbb\xbf")
    bad = [(i, b) for i, b in enumerate(data) if b > 0x7F]
    assert not bad, f"non-ASCII bytes at offsets {bad[:5]}"


def test_every_language_decodes_with_same_keys_and_placeholders():
    msgs = _messages()
    assert set(msgs) == {"en", "ru", "zh-TW"}
    for lang, m in msgs.items():
        assert set(m) == KEYS, lang
        for k in KEYS:
            assert sorted(re.findall(r"\{\d\}", m[k])) == sorted(re.findall(r"\{\d\}", msgs["en"][k])), (lang, k)
    assert msgs["ru"]["start"].startswith("Установка tinglongWu")
    assert msgs["zh-TW"]["start"].startswith("安裝 tinglongWu")
    assert msgs["zh-TW"]["dirty"].endswith("請改名或設定 TLWU_DIR。")


@pytest.mark.skipif(not shutil.which("pwsh"), reason="pwsh not installed")
@pytest.mark.parametrize("lang,expected", [("ru", "существует"), ("zh-TW", "已存在但不是"), ("en", "exists but is not")])
def test_pwsh_prints_decoded_message(tmp_path, lang, expected):
    target = tmp_path / "notours"
    target.mkdir()
    (target / "x.txt").write_text("x")
    env = {"PATH": "/usr/bin:/bin", "TLWU_UI_LANG": lang, "TLWU_DIR": str(target), "USERPROFILE": str(tmp_path)}
    r = subprocess.run(["pwsh", "-NoProfile", "-File", str(PS1)], capture_output=True, env=env, timeout=120)
    out = r.stdout.decode("utf-8", "replace")
    assert expected in out, out
