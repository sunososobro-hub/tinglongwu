"""#1195: the CPU-vs-Vulkan parity check passes only for a real Vulkan run with matching text (behaviour tests)."""
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import vulkan_parity as vp  # noqa: E402

CPU = {"backend": "cpu", "texts": ["會議明天早上九點開始。", "請在本週結束前把報告寄給我。"], "seconds": 3.0}


def test_identical_vulkan_output_passes():
    ok, md = vp.compare(CPU, {**CPU, "backend": "vulkan"})
    assert ok and "100.00" in md


def test_silent_fallback_to_cpu_fails():
    ok, md = vp.compare(CPU, dict(CPU))
    assert not ok and "silent fallback" in md


def test_different_text_fails():
    bad = {"backend": "vulkan", "texts": ["完全不同的句子在這裡出現。", "另一個毫無關聯的翻譯結果。"], "seconds": 1}
    ok, md = vp.compare(CPU, bad)
    assert not ok and "chrF" in md


def test_empty_or_mismatched_outputs_fail():
    assert not vp.compare(CPU, {"backend": "vulkan", "texts": [], "seconds": 1})[0]


def test_cli_compare_exit_codes(tmp_path):
    a, b = tmp_path / "c.json", tmp_path / "v.json"
    a.write_text(json.dumps(CPU), "utf-8")
    b.write_text(json.dumps({**CPU, "backend": "vulkan"}), "utf-8")
    script = str(Path(vp.__file__))
    assert subprocess.run([sys.executable, script, "compare", str(a), str(b)], capture_output=True).returncode == 0
    assert subprocess.run([sys.executable, script, "compare", str(a), str(a)], capture_output=True).returncode == 1


def test_summary_lists_each_line_and_marks_the_divergent_one():
    vk = {"backend": "vulkan", "texts": [CPU["texts"][0], "請在週末前把報告寄給我。"], "seconds": 1}
    ok, md = vp.compare(CPU, vk)
    rows = [r for r in md.splitlines() if r.startswith("| 1 ") or r.startswith("| 2 ")]
    assert len(rows) == 2
    assert CPU["texts"][0] in rows[0] and rows[0].endswith("| yes |")
    assert CPU["texts"][1] in rows[1] and "請在週末前把報告寄給我。" in rows[1] and rows[1].endswith("| NO |")
    assert vp.SENTENCES[1] in rows[1]


def test_summary_escapes_pipes_and_handles_missing_lines():
    ok, md = vp.compare({"backend": "cpu", "texts": ["a|b"], "seconds": 1}, {"backend": "vulkan", "texts": [], "seconds": 1})
    assert not ok
    row = next(r for r in md.splitlines() if r.startswith("| 1 "))
    assert "a\\|b" in row and row.endswith("| NO |")
