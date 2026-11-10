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
