"""#342: (1) every heading removed from a README is present somewhere in docs/; (2) relative links in README.md + docs/*.md resolve.

Usage: python scripts/check_readme_docs.py [OLD_README_PATH]   (exit 0 = ok)
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _norm(h: str) -> str:
    return re.sub(r"[^\w ]", "", h.lower()).strip()


def headings(text: str) -> list[str]:
    return re.findall(r"^#+ (.+)$", text, re.M)


def missing_headings(old_text: str, new_text: str, docs_dir: Path) -> list[str]:
    have = {_norm(h) for h in headings(new_text)}
    for md in docs_dir.glob("*.md"):
        have |= {_norm(h) for h in headings(md.read_text("utf-8"))}
    return [h for h in headings(old_text) if _norm(h) not in have]


def broken_links(files: list[Path]) -> list[str]:
    bad = []
    for md in files:
        for target in re.findall(r"\]\(([^)\s]+)\)", md.read_text("utf-8")):
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            path = target.partition("#")[0]
            if path and not (md.parent / path).resolve().exists():
                bad.append(f"{md.name}: {target}")
    return bad


# #703: in the Chinese full READMEs, beginners must see setup.exe first; manual `tlwu-setup --...` commands only
# appear below the "advanced" heading.
ADVANCED_HEADINGS = {"readme-full.zh-TW.md": "## 開發者／進階安裝", "readme-full.zh-CN.md": "## 开发者／进阶安装"}
SETUP_CMD = re.compile(r"tlwu-setup\s+--")


def setup_cmds_above_advanced(text: str, heading: str) -> list[str]:
    """Lines with a `tlwu-setup --...` command above `heading`; the heading itself missing is reported too."""
    lines = text.splitlines()
    if heading not in lines:
        return [f"missing heading: {heading}"]
    return [l for l in lines[: lines.index(heading)] if SETUP_CMD.search(l)]


def main(argv: list[str]) -> int:
    rc = 0
    for name, heading in ADVANCED_HEADINGS.items():
        for line in setup_cmds_above_advanced((ROOT / "docs" / name).read_text("utf-8"), heading):
            print(f"{name}: above advanced heading:", line.strip())
            rc = 1
    if argv:
        old = Path(argv[0]).read_text("utf-8")
        miss = missing_headings(old, (ROOT / "README.md").read_text("utf-8"), ROOT / "docs")
        for h in miss:
            print("heading missing:", h)
        rc |= bool(miss)
    bad = broken_links([ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))])
    for b in bad:
        print("broken link:", b)
    return int(rc or bool(bad))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
