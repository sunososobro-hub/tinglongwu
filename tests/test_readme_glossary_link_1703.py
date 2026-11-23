import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_readme_links_existing_glossary_doc():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    targets = re.findall(r"\]\((docs/glossary-with-ai\.md)\)", text)
    assert targets, "README must link docs/glossary-with-ai.md"
    for t in targets:
        assert (ROOT / t).is_file()
