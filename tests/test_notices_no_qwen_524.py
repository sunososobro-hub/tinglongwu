"""#524: the exe's THIRD_PARTY_NOTICES.txt must name the models we actually download (Hy-MT2), not the removed Qwen."""
import importlib.util
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "packaging" / "build_windows.py"


def _load():
    spec = importlib.util.spec_from_file_location("build_windows", SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_written_notices_list_hymt2_not_qwen(tmp_path):
    mod = _load()
    mod.write_licenses(tmp_path, None)
    text = (tmp_path / "licenses" / "THIRD_PARTY_NOTICES.txt").read_text("utf-8")
    head = text.split("Native components")[0]
    assert "Hy-MT2: Apache-2.0" in head
    assert "qwen" not in head.lower()
