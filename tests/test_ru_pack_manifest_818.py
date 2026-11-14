"""#818: the ru pack (Hy-MT2 + Piper dmitri) is listed in packaging/packs.json with a summed size."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location("make_pack_manifest", ROOT / "scripts" / "make_pack_manifest.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_ru_entry_size_is_hymt_plus_both_piper_files():
    m = _load()
    ent = m.ru_entry()
    assert ent["size"] == m.hymt_entry()["size"] + 63221984 + 5465
    assert len(ent["sha256"]) == 64


def test_checked_in_packs_json_lists_ru_and_is_current():
    m = _load()
    committed = json.loads((ROOT / "packaging" / "packs.json").read_text("utf-8"))
    assert committed["packs"]["ru"]["size"] == m.ru_entry()["size"]
    assert m.main(["--check"]) == 0
