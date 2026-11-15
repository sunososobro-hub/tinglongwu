"""#966: `tlwu-setup --gpu-pack` installs the release's GPU pack (size+sha256 from its manifest) and the first-run
wizard calls it on NVIDIA machines (or says why it did not)."""
from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path

from tinglongwu_setup import gpu_pack, selfupdate
from tlwu_app import setup_gui, tiers

VERSION = "0.3.5"
ZIP_URL = "https://example.invalid/tinglongwu-gpu-pack.zip"
MAN_URL = ZIP_URL + ".manifest.json"


def make_zip(files=None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n, b in (files or {"cublas64_12.dll": b"x", "cudnn64_9.dll": b"y"}).items():
            z.writestr(n, b)
    return buf.getvalue()


class Release:
    def __init__(self, data: bytes | None, man_sha: str | None = "auto", with_manifest=True):
        assets = []
        if data is not None:
            assets.append({"name": gpu_pack.ASSET, "browser_download_url": ZIP_URL})
        if with_manifest:
            assets.append({"name": gpu_pack.MANIFEST_ASSET, "browser_download_url": MAN_URL})
        self.rel = {"tag_name": f"v{VERSION}", "prerelease": False, "draft": False, "assets": assets}
        self.data = data or b""
        sha = hashlib.sha256(self.data).hexdigest() if man_sha == "auto" else man_sha
        self.man = {"name": "gpu-pack", "size": len(self.data), "sha256": sha, "url": ZIP_URL}

    def get(self, url, timeout=0):
        if url == selfupdate.API_TAG.format(version=VERSION):
            return json.dumps(self.rel).encode()
        if url == MAN_URL:
            return json.dumps(self.man).encode()
        raise AssertionError(url)

    def fetch(self, url, dest, sha256, **kw):
        dest.write_bytes(self.data)
        return dest


def run(rel, dest, version=VERSION):
    lines = []
    rc = gpu_pack.run(dest, version, get=rel.get, fetch=rel.fetch, out=lines.append, env={})
    return rc, lines


def test_installs_pack_where_runtime_looks(tmp_path):
    from tinglongwu import gpu_pack as runtime_pack

    dest = tmp_path / "gpu-pack"
    rc, _ = run(Release(make_zip()), dest)
    assert rc == 0 and (dest / "cublas64_12.dll").exists()
    assert runtime_pack.pack_dir(dest) is not None


def test_bad_sha_installs_nothing(tmp_path):
    dest = tmp_path / "gpu-pack"
    rc, lines = run(Release(make_zip(), man_sha="0" * 64), dest)
    assert rc == 1 and not dest.exists() and "FAIL" in lines[-1]


def test_zip_slip_refused(tmp_path):
    dest = tmp_path / "gpu-pack"
    rc, lines = run(Release(make_zip({"../evil.dll": b"x"})), dest)
    assert rc == 1 and not dest.exists() and not (tmp_path / "evil.dll").exists()


def test_release_without_pack_is_a_note(tmp_path):
    rc, lines = run(Release(None), tmp_path / "gpu-pack")
    assert rc == 0 and lines == [gpu_pack.NO_PACK_NOTE]


def test_missing_manifest_refuses(tmp_path):
    rc, lines = run(Release(make_zip(), with_manifest=False), tmp_path / "gpu-pack")
    assert rc == 1 and "manifest" in lines[-1]


def test_offline_flag(tmp_path):
    rc = gpu_pack.run(tmp_path / "g", VERSION, out=lambda s: None, env={"TLWU_OFFLINE": "1"})
    assert rc == 1


def test_wizard_installs_pack_only_for_nvidia_without_cpu_mode():
    gpu = tiers.Gpu(name="RTX", total_mb=8000, free_mb=7000, driver="560.94")
    assert setup_gui.wants_gpu_pack(tiers.choose_tier(gpu, False), gpu)
    assert not setup_gui.wants_gpu_pack(tiers.choose_tier(gpu, True), gpu)  # user picked CPU mode
    assert not setup_gui.wants_gpu_pack(tiers.choose_tier(None, False), None)  # no NVIDIA GPU


def test_wizard_summary_says_installed_or_why_not():
    assert "已安裝" in setup_gui.gpu_pack_status(0, "gpu pack 0.3.5: installed in x")
    assert "沒有 GPU 加速包" in setup_gui.gpu_pack_status(0, gpu_pack.NO_PACK_NOTE)
    assert "沒有安裝成功" in setup_gui.gpu_pack_status(1, "gpu pack: FAIL")
    assert setup_gui.gpu_pack_cmd(False, Path("m"))[-3:-1] == ["--gpu-pack", "--models-dir"]
