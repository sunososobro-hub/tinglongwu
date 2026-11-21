"""#945: the release attaches tinglongwu-app-layer.zip + manifest (runtime_key); release_check --app-layer checks it."""
import importlib.util
import json
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import release_check  # noqa: E402


def _bw():
    spec = importlib.util.spec_from_file_location("build_windows", ROOT / "release" / "packaging" / "build_windows.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _fake_dist(root: Path, version="1.2.3", key="rk-abc", lite=True) -> Path:
    app = root / "tlwu"
    for pkg in ("tinglongwu", "tlwu_app", "tinglongwu_setup"):
        (app / pkg / "sub").mkdir(parents=True)
        (app / pkg / "__init__.py").write_text("x = 1\n")
        (app / pkg / "sub" / "m.pyc").write_bytes(b"pyc")
    # runtime layer files that must NOT be in the zip
    (app / "python312.dll").write_bytes(b"py")
    (app / "DLLs").mkdir()
    (app / "DLLs" / "_ssl.pyd").write_bytes(b"d")
    (app / "site-packages" / "numpy").mkdir(parents=True)
    (app / "site-packages" / "numpy" / "__init__.pyc").write_bytes(b"n")
    (app / "tlwu.exe").write_bytes(b"MZ")
    (app / "使用說明.txt").write_text("hi", "utf-8")
    (app / "app-layer.json").write_text(json.dumps({"version": version, "packages": ["tinglongwu"]}))
    (app / "runtime-layer.json").write_text(json.dumps({"key": key, "lite": lite, "python": "3.12.4"}))
    return app


def test_zip_holds_only_the_app_layer_and_manifest_has_runtime_key(tmp_path):
    bw = _bw()
    app = _fake_dist(tmp_path)
    man_path = bw.make_app_layer_zip(app, tmp_path / "out")
    zp = tmp_path / "out" / bw.APP_LAYER_ASSET
    assert bw.APP_LAYER_ASSET == "tinglongwu-app-layer.zip" == release_check.APP_LAYER_ASSET
    names = set(zipfile.ZipFile(zp).namelist())
    assert {"tlwu.exe", "app-layer.json", "使用說明.txt", "tinglongwu/__init__.py", "tlwu_app/sub/m.pyc",
            "tinglongwu_setup/__init__.py"} <= names
    assert not [n for n in names if n.startswith(("DLLs", "site-packages")) or n.endswith((".dll", ".pyd"))]
    assert "runtime-layer.json" not in names
    man = json.loads(man_path.read_text("utf-8"))
    assert set(man) == set(bw.APP_LAYER_MANIFEST_KEYS if hasattr(bw, "APP_LAYER_MANIFEST_KEYS")
                           else release_check.APP_LAYER_MANIFEST_KEYS)
    assert man["runtime_key"] == "rk-abc" and man["version"] == "1.2.3" and man["lite"] is True
    assert man["size"] == zp.stat().st_size and man["name"] == "tinglongwu-app-layer.zip"
    assert release_check.app_layer_problems(zp, "v1.2.3") == []


def test_runtime_key_follows_the_runtime_layer_manifest(tmp_path):
    bw = _bw()
    app = _fake_dist(tmp_path, key="other-key", lite=False)
    man = json.loads(bw.make_app_layer_zip(app, tmp_path / "o").read_text("utf-8"))
    assert man["runtime_key"] == "other-key" and man["lite"] is False


def test_missing_runtime_manifest_stops_the_build(tmp_path):
    bw = _bw()
    app = _fake_dist(tmp_path)
    (app / "runtime-layer.json").unlink()
    with pytest.raises(SystemExit):
        bw.make_app_layer_zip(app, tmp_path / "o")


def _built(tmp_path, version="1.2.3"):
    bw = _bw()
    zp = tmp_path / "out" / bw.APP_LAYER_ASSET
    bw.make_app_layer_zip(_fake_dist(tmp_path, version=version), tmp_path / "out")
    return zp


def test_release_check_accepts_a_good_zip_via_cli(tmp_path):
    zp = _built(tmp_path)
    assert release_check.main(["--app-layer", str(zp), "--tag", "v1.2.3"]) == 0


def test_release_check_rejects_version_mismatch(tmp_path):
    zp = _built(tmp_path, version="1.2.2")
    assert release_check.main(["--app-layer", str(zp), "--tag", "v1.2.3"]) == 1


def test_release_check_rejects_sha_and_size_mismatch(tmp_path):
    zp = _built(tmp_path)
    man_path = zp.with_name(zp.name + ".manifest.json")
    man = json.loads(man_path.read_text("utf-8"))
    man.update(sha256="0" * 64, size=1)
    man_path.write_text(json.dumps(man), "utf-8")
    probs = release_check.app_layer_problems(zp, "v1.2.3")
    assert any("sha256" in p for p in probs) and any("size" in p for p in probs)


def test_release_check_rejects_runtime_files_in_zip(tmp_path):
    zp = _built(tmp_path)
    with zipfile.ZipFile(zp, "a") as z:
        z.writestr("python312.dll", b"py")
    assert any("runtime-layer" in p for p in release_check.app_layer_problems(zp))


def test_release_check_rejects_oversized_zip(tmp_path):
    zp = _built(tmp_path)
    assert any("budget" in p for p in release_check.app_layer_problems(zp, max_mb=0.000001))


def test_release_check_missing_zip_is_exit_2(tmp_path):
    assert release_check.main(["--app-layer", str(tmp_path / "nope.zip")]) == 2
