from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_readmes_have_status_table():
    for name, head in (("README.md", "## Verification status"),
                       ("README.zh-TW.md", "## 驗證狀態"),
                       ("README.zh-CN.md", "## 验证状态")):
        s = (ROOT / name).read_text("utf-8")
        assert head in s, name
        assert "| | Windows | macOS | Linux |" in s, name
        assert "issues/8" in s and "🧪" in s and "✅" in s, name


def test_zh_cn_readme_has_all_one_line_installs():
    en = (ROOT / "README.md").read_text("utf-8")
    cn = (ROOT / "README.zh-CN.md").read_text("utf-8")
    for cmd in ("irm https://raw.githubusercontent.com/sunososobro-hub/tinglongwu/main/scripts/bootstrap_windows.ps1",
                "curl -fsSL https://raw.githubusercontent.com/sunososobro-hub/tinglongwu/main/scripts/install.sh \\| bash"):
        assert cmd in en
        assert cmd in cn
