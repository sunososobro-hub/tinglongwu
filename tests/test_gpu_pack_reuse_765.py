"""#765: the release reuses the previous release's GPU pack when the pinned wheels did not change."""
import hashlib
import importlib.util
import json
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "scripts" / "gpu_pack_reuse.py"
spec = importlib.util.spec_from_file_location("gpu_pack_reuse", SRC)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

REPO = "o/r"
DATA = b"zipdata" * 10


def _fake(key="K", size=None, data=DATA, sha=None, has_manifest=True, release=True, name=True):
    man = {"size": len(data) if size is None else size, "sha256": sha or hashlib.sha256(data).hexdigest(),
           "url": f"https://github.com/{REPO}/releases/download/v1.0.0/{m.GPU_PACK_ASSET}"}
    if name:
        man["name"] = "gpu-pack"
    if key is not None:
        man["wheels_key"] = key
    assets = [{"name": m.GPU_PACK_ASSET, "browser_download_url": "u://zip"}]
    if has_manifest:
        assets.append({"name": m.MANIFEST_ASSET, "browser_download_url": "u://man"})

    def get(url):
        if url.endswith("/releases/latest"):
            if not release:
                raise OSError("404")
            return json.dumps({"tag_name": "v1.0.0", "assets": assets}).encode()
        return json.dumps(man).encode() if url == "u://man" else data

    return get


def test_same_key_reuses_identical_zip_and_retags_manifest(tmp_path):
    ok, text = m.try_reuse(REPO, "v1.1.0", "K", tmp_path, _fake())
    assert (ok, text) == (True, "v1.0.0")
    assert (tmp_path / m.GPU_PACK_ASSET).read_bytes() == DATA
    man = json.loads((tmp_path / m.MANIFEST_ASSET).read_text())
    assert man["sha256"] == hashlib.sha256(DATA).hexdigest()
    assert man["url"].endswith("/releases/download/v1.1.0/" + m.GPU_PACK_ASSET)


def test_changed_key_builds(tmp_path):
    ok, text = m.try_reuse(REPO, "v1.1.0", "OTHER", tmp_path, _fake())
    assert not ok and "changed" in text and not list(tmp_path.iterdir())


def test_no_release_missing_or_invalid_manifest_build(tmp_path):
    assert not m.try_reuse(REPO, "v2", "K", tmp_path, _fake(release=False))[0]
    assert not m.try_reuse(REPO, "v2", "K", tmp_path, _fake(has_manifest=False))[0]
    assert not m.try_reuse(REPO, "v2", "K", tmp_path, _fake(key=None))[0]
    assert not m.try_reuse(REPO, "v2", "K", tmp_path, _fake(sha="0" * 64))[0]
    assert not m.try_reuse(REPO, "v2", "K", tmp_path, _fake(name=False))[0]
    assert not m.try_reuse(REPO, "v2", "K", tmp_path, _fake(size=str(len(DATA))))[0]


def test_cli_prints_summary_lines(tmp_path, capsys):
    assert m.main(["--repo", REPO, "--tag", "v1.1.0", "--out", str(tmp_path), "--key", "K"], _fake()) == 0
    out = capsys.readouterr().out
    assert "reused=true" in out and "summary=gpu pack: reused from v1.0.0" in out
    m.main(["--repo", REPO, "--tag", "v1.1.0", "--out", str(tmp_path / "x"), "--key", "Z"], _fake())
    out = capsys.readouterr().out
    assert "reused=false" in out and "summary=gpu pack: built (pinned wheels changed)" in out
