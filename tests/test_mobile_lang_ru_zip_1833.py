"""#1833: optional lang-ru group in the mobile models manifest + make_models_zip builds the ru zip."""
import hashlib
import importlib.util
import json
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = json.loads((ROOT / "mobile/assets/models_manifest.json").read_text("utf-8"))
RU = MANIFEST["optional_groups"]["lang-ru"]


def _tool():
    spec = importlib.util.spec_from_file_location("make_models_zip", ROOT / "mobile/tool/make_models_zip.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fake(tmp_path, tamper=None):
    """Build a fake models folder whose files match the manifest pins, by patching the manifest sizes/hashes."""
    models, lic = tmp_path / "models", tmp_path / "lic"
    models.mkdir()
    lic.mkdir()
    (lic / RU["license"]).write_text("MIT\n")
    files = []
    for i, e in enumerate(RU["files"]):
        data = f"fake-{i}".encode()
        p = models / e["path"]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data if e["path"] != tamper else b"x" * len(data))
        files.append({**e, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    return models, lic, files


def _run(mod, monkeypatch, files, models, lic, out):
    m = json.loads(json.dumps(MANIFEST))
    m["optional_groups"]["lang-ru"]["files"] = files
    monkeypatch.setattr(mod, "_manifest", lambda: m)
    mod.main_ru(str(models), str(lic), str(out))


def test_ru_group_pins_match_eval_gigaam():
    src_pins = None
    try:
        import sys
        sys.path[:0] = [str(ROOT / "eval"), str(ROOT / "src/core")]
        from tlwu_eval import gigaam
        src_pins = gigaam.FILES
    except Exception:
        pytest.skip("eval package not importable here")
    got = {e["path"].split("/", 1)[1]: (e["size"], e["sha256"]) for e in RU["files"]}
    assert got == src_pins


def test_default_files_have_no_lang_ru():
    assert all(e.get("group") != "lang-ru" for e in MANIFEST["files"])
    assert {e["group"] for e in RU["files"]} == {"lang-ru"}


def test_ru_zip_lists_exactly_lang_ru_files_and_licence(tmp_path, monkeypatch):
    mod = _tool()
    models, lic, files = _fake(tmp_path)
    out = tmp_path / "ru.zip"
    _run(mod, monkeypatch, files, models, lic, out)
    names = set(zipfile.ZipFile(out).namelist())
    expect = {mod.RU_PREFIX + e["path"] for e in RU["files"]}
    expect |= {mod.RU_PREFIX + "NOTICE.txt", mod.RU_PREFIX + "LICENSES/" + RU["license"]}
    assert names == expect


def test_ru_zip_refuses_wrong_sha256(tmp_path, monkeypatch):
    mod = _tool()
    models, lic, files = _fake(tmp_path, tamper=RU["files"][0]["path"])
    with pytest.raises(SystemExit) as e:
        _run(mod, monkeypatch, files, models, lic, tmp_path / "ru.zip")
    assert "do not match" in str(e.value)
    assert not (tmp_path / "ru.zip").exists()
