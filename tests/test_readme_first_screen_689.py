"""#689: the first screen of every README is visual-first: at most 25 words of prose above the fold."""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NAMES = ("README.md", "README.zh-TW.md", "README.zh-CN.md")
MAX_WORDS = 25
CJK = re.compile(r"[㐀-鿿가-힯぀-ヿ]")


def first_screen(name: str) -> str:
    text = (ROOT / name).read_text("utf-8")
    return text.split("<details>", 1)[0]


def prose_words(md: str) -> int:
    md = re.sub(r"```.*?```", lambda m: re.sub(r"flowchart \w+|-->|[A-C]\[|\]", " ", m.group(0).replace("```mermaid", "").replace("```", "")), md, flags=re.S)
    md = re.sub(r"!\[[^\]]*\]\([^)]*\)|<img[^>]*>", " ", md)  # badges and images  # images carry alt text, not visible prose
    md = re.sub(r"\(https?://[^)]*\)|href=\"[^\"]*\"|\(README[^)]*\)", " ", md)
    md = re.sub(r"<[^>]+>|[|:\-#*]+", " ", md)
    latin = len(re.findall(r"[A-Za-z0-9]+", CJK.sub(" ", md)))
    return latin + (len(CJK.findall(md)) + 1) // 2


@pytest.mark.parametrize("name", NAMES)
def test_first_screen_prose_budget(name):
    assert prose_words(first_screen(name)) <= MAX_WORDS, name


@pytest.mark.parametrize("name", NAMES)
def test_first_screen_has_gif_flow_modes_and_download(name):
    s = first_screen(name)
    assert re.search(r'<img src="assets/demo/[^"]+\.gif" alt="[^"]+"', s)  # image under assets/ with alt text
    assert "```mermaid" in s and s.count("-->") == 2  # three nodes
    assert all(icon in s for icon in ("🎧", "🗣️", "🔁"))
    assert "releases/latest" in s  # download button
    assert "<details>" in (ROOT / name).read_text("utf-8")  # the rest is folded


def test_budget_counter_notices_prose():
    assert prose_words("# one two three\n" + "word " * 30) > MAX_WORDS
    assert prose_words("# 聽龍\n| 🎧 Listen |") <= 4
