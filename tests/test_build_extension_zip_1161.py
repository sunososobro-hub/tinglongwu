"""#1161: the Web Store zip has the right manifest version and only runtime files."""
import importlib.util
import json
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("build_extension_zip", ROOT / "scripts" / "build_extension_zip.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_zip_contents_and_version(tmp_path):
    mod = _load()
    out = mod.build(tmp_path)
    import tinglongwu

    assert out.name == f"tlwu-extension-{tinglongwu.__version__}.zip"
    with zipfile.ZipFile(out) as z:
        names = set(z.namelist())
        manifest = json.loads(z.read("manifest.json"))
    assert manifest["version"] == tinglongwu.__version__
    assert "(dev)" not in manifest["name"]
    assert {"manifest.json", "sw.js", "offscreen.html", "offscreen.js", "overlay.js", "recognizer.js"} <= names
    assert not any(n.startswith(("spike/", "tests/")) or n.endswith(".md") for n in names)
    assert out.stat().st_size <= mod.MAX_ZIP_BYTES
    # every file the manifest references is in the zip
    refs = [manifest["background"]["service_worker"]] + [j for c in manifest["content_scripts"] for j in c["js"]]
    assert set(refs) <= names


def test_cli_writes_zip(tmp_path):
    r = subprocess.run([sys.executable, "-I", str(ROOT / "scripts" / "build_extension_zip.py"), "--out-dir", str(tmp_path)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert list(tmp_path.glob("tlwu-extension-*.zip"))
    # the checked-in manifest is untouched
    assert "(dev)" in json.loads((ROOT / "web" / "extension" / "capture" / "manifest.json").read_text("utf-8"))["name"]


def test_out_dir_inside_extension_is_rejected(tmp_path):
    import pytest

    mod = _load()
    ext = tmp_path / "ext"
    ext.mkdir()
    (ext / "manifest.json").write_text('{"name": "x (dev)", "version": "0"}')
    with pytest.raises(SystemExit):
        mod.build(ext / "dist", ext_dir=ext, version="1.0.0")
    assert not (ext / "dist").exists()
