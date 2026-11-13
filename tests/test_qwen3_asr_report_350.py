"""#350: the Qwen3-ASR research report is reachable from the FAQ, its links resolve and its chart is valid SVG."""
import importlib.util
import re
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "docs" / "qwen3-asr.md"


def _checker():
    spec = importlib.util.spec_from_file_location("check_readme_docs", ROOT / "scripts" / "check_readme_docs.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_faq_and_report_links_resolve():
    assert REPORT.is_file()
    assert _checker().broken_links([ROOT / "docs" / "faq.md", REPORT]) == []


def test_faq_links_report():
    links = re.findall(r"\]\(([^)#\s]+)", (ROOT / "docs" / "faq.md").read_text("utf-8"))
    assert "qwen3-asr.md" in links


def test_report_chart_is_valid_svg():
    imgs = re.findall(r"!\[[^\]]*\]\(([^)\s]+\.svg)\)", REPORT.read_text("utf-8"))
    assert imgs, "report must embed a chart"
    root = ET.parse(REPORT.parent / imgs[0]).getroot()
    assert root.tag.endswith("svg")
    assert len(root.findall("{http://www.w3.org/2000/svg}rect")) >= 8  # bars + legend
