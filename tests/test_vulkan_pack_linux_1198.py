"""#1198 (2): a Linux Vulkan llama.cpp pack. scripts/make_vulkan_pack.py packs a Linux GGML_VULKAN=on build as
tlwu-vulkan-pack-linux-<version>.zip, and `tlwu-setup --vulkan-pack` on Linux installs that zip, never the Windows one."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import urllib.error
import zipfile
from pathlib import Path

import pytest

from tinglongwu import __version__, mt_backend
from tinglongwu_setup import selfupdate, vulkan_pack

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("make_vulkan_pack", ROOT / "scripts" / "make_vulkan_pack.py")
mvp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mvp)

VERSION = __version__
WIN_ZIP = f"tlwu-vulkan-pack-{VERSION}.zip"
LINUX_ZIP = f"tlwu-vulkan-pack-linux-{VERSION}.zip"
LINUX_LIBS = ("libllama.so", "libggml-vulkan.so", "libggml.so", "libggml-base.so", "libggml-cpu.so")


def linux_lib(tmp_path: Path, names=LINUX_LIBS) -> Path:
    lib = tmp_path / "llama_cpp" / "lib"
    lib.mkdir(parents=True)
    for n in names:
        (lib / n).write_bytes(n.encode())
    (lib / "llama.pc").write_text("not a library")  # only shared libraries are packed
    return lib


def test_pip_target_copies_keep_the_soname_and_drop_the_full_version(tmp_path):
    """pip --target turns libX.so -> libX.so.0 -> libX.so.0.25.3 symlinks into 3 full copies; the libraries link
    against the SONAME libX.so.0 and llama-cpp-python loads libX.so, so the third copy only costs ~14 MB."""
    names = ("libllama.so", "libllama.so.0", "libllama.so.0.5.0", "libggml-vulkan.so", "libggml-vulkan.so.0",
             "libggml-vulkan.so.0.25.3")
    z = mvp.build(linux_lib(tmp_path, names), tmp_path / "dist", VERSION)
    libs = [n for n in mvp.layout(z) if ".so" in n]
    assert libs == sorted(["libllama.so", "libllama.so.0", "libggml-vulkan.so", "libggml-vulkan.so.0"])


def test_linux_build_is_packed_as_the_linux_zip_and_the_app_accepts_it(tmp_path, capsys):
    z = mvp.build(linux_lib(tmp_path), tmp_path / "dist", VERSION)
    assert z.name == LINUX_ZIP
    names = mvp.layout(z)
    assert names == sorted([*LINUX_LIBS, mvp.MANIFEST_NAME, mvp.VERSION_FILE])
    with zipfile.ZipFile(z) as zz:
        manifest = json.loads(zz.read(mvp.MANIFEST_NAME))
    assert manifest["platform"] == "linux" and manifest["version"] == VERSION
    assert mvp.main(["--check", str(z)]) == 0
    assert "vulkan_pack_dir: accepted" in capsys.readouterr().out
    out = tmp_path / "unzipped"
    zipfile.ZipFile(z).extractall(out)
    assert mt_backend.vulkan_pack_dir(out) == out.resolve()


def test_windows_build_keeps_its_800_name(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    for n in ("llama.dll", "ggml-vulkan.dll", "ggml.dll"):
        (lib / n).write_bytes(b"x")
    assert mvp.build(lib, tmp_path / "dist", VERSION).name == WIN_ZIP


def test_linux_build_without_the_vulkan_backend_is_not_packed(tmp_path):
    with pytest.raises(SystemExit, match="libggml-vulkan.so"):
        mvp.build(linux_lib(tmp_path, ("libllama.so", "libggml.so")), tmp_path / "dist", VERSION)


class Release:
    """A fake release carrying the given pack zips (name -> bytes) and their SHA256SUMS.txt lines."""

    def __init__(self, zips: dict[str, bytes]):
        self.zips = zips
        assets = [{"name": n, "browser_download_url": f"https://example.invalid/{n}"} for n in zips]
        assets.append({"name": selfupdate.SUMS_NAME, "browser_download_url": "https://example.invalid/sums"})
        self.rel = {"tag_name": f"v{VERSION}", "prerelease": False, "draft": False, "assets": assets}
        self.fetched = []

    def get(self, url, timeout=0):
        if url == selfupdate.API_TAG.format(version=VERSION):
            return json.dumps(self.rel).encode()
        if url.startswith("https://api.github.com/"):
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        return "".join(f"{hashlib.sha256(b).hexdigest()}  {n}\n" for n, b in self.zips.items()).encode()

    def fetch(self, url, dest, sha):
        self.fetched.append(url.rsplit("/", 1)[1])
        dest.write_bytes(self.zips[dest.name])
        return dest

    def run(self, dest):
        out = []
        return vulkan_pack.run(dest, VERSION, get=self.get, fetch=self.fetch, out=out.append, env={}), out


def zip_bytes(tmp_path: Path, name: str, files) -> bytes:
    p = tmp_path / name
    with zipfile.ZipFile(p, "w") as z:
        for n in files:
            z.writestr(n, b"x")
    return p.read_bytes()


def test_linux_setup_installs_the_linux_pack_not_the_windows_one(tmp_path, monkeypatch):
    monkeypatch.setattr(vulkan_pack, "HOST", "linux")
    rel = Release({WIN_ZIP: zip_bytes(tmp_path, WIN_ZIP, ("llama.dll", "ggml-vulkan.dll")),
                   LINUX_ZIP: zip_bytes(tmp_path, LINUX_ZIP, ("libllama.so", "libggml-vulkan.so"))})
    dest = tmp_path / "app" / "vulkan-pack"
    rc, out = rel.run(dest)
    assert rc == 0, out
    assert rel.fetched == [LINUX_ZIP]
    assert (dest / "libllama.so").is_file() and not (dest / "llama.dll").exists()
    assert mt_backend.vulkan_pack_dir(dest) == dest.resolve()


def test_linux_setup_never_installs_a_windows_only_release(tmp_path, monkeypatch):
    """Before #1198 Linux downloaded the Windows DLL zip and installed it as its Vulkan pack."""
    monkeypatch.setattr(vulkan_pack, "HOST", "linux")
    rel = Release({WIN_ZIP: zip_bytes(tmp_path, WIN_ZIP, ("llama.dll", "ggml-vulkan.dll"))})
    dest = tmp_path / "app" / "vulkan-pack"
    rc, out = rel.run(dest)
    assert rc == 0 and rel.fetched == [] and not dest.exists()
    assert out == [vulkan_pack.NO_PACK_NOTE]


def test_linux_install_zip_refuses_a_pack_without_libllama(tmp_path, monkeypatch):
    monkeypatch.setattr(vulkan_pack, "HOST", "linux")
    b = zip_bytes(tmp_path, "x.zip", ("llama.dll", "ggml-vulkan.dll"))
    with pytest.raises(vulkan_pack.PackError, match="no llama library"):
        vulkan_pack.install_zip(tmp_path / "x.zip", hashlib.sha256(b).hexdigest(), tmp_path / "vulkan-pack")
    assert not (tmp_path / "vulkan-pack").exists()


def test_macos_has_no_vulkan_pack(tmp_path, monkeypatch):
    monkeypatch.setattr(vulkan_pack, "HOST", "darwin")
    rel = Release({WIN_ZIP: zip_bytes(tmp_path, WIN_ZIP, ("llama.dll",))})
    rc, out = rel.run(tmp_path / "vulkan-pack")
    assert rc == 0 and rel.fetched == [] and out == [vulkan_pack.NO_PACK_NOTE]


class FakeVk:
    """Stand-in for libggml-vulkan.so: `devices` is what ggml_backend_vk_* reports."""

    def __init__(self, devices):
        self.devices = devices
        self.ggml_backend_vk_get_device_count = lambda: len(self.devices)
        # plain functions, not bound methods: the code sets .restype / .argtypes on them like on ctypes functions
        self.ggml_backend_vk_get_device_description = lambda i, buf, n: setattr(buf, "value", self.devices[i].encode())


def test_smoke_lists_the_vulkan_devices_the_pack_sees(tmp_path):
    z = mvp.build(linux_lib(tmp_path), tmp_path / "dist", VERSION)
    loaded, lines = [], []
    rc = mvp.smoke(z, load=lambda p: loaded.append(Path(p).name) or FakeVk(["llvmpipe (LLVM 20.1.2, 256 bits)"]),
                   out=lines.append)
    assert rc == 0 and loaded == ["libggml-vulkan.so"]
    assert lines == ["vulkan device 0: llvmpipe (LLVM 20.1.2, 256 bits)", "vulkan smoke: 1 device(s)"]


def test_smoke_fails_without_a_device_or_a_loadable_backend(tmp_path):
    z = mvp.build(linux_lib(tmp_path), tmp_path / "dist", VERSION)
    lines = []
    assert mvp.smoke(z, load=lambda p: FakeVk([]), out=lines.append) == 1
    assert lines[-1] == "vulkan smoke: 0 device(s) - FAIL"

    def broken(p):
        raise OSError("libvulkan.so.1: cannot open shared object file")
    assert mvp.smoke(z, load=broken, out=lines.append) == 1
    assert lines[-1].startswith("vulkan smoke: FAIL OSError")


_spec800 = importlib.util.spec_from_file_location("vk800", ROOT / "tests" / "test_make_vulkan_pack_800.py")
vk800 = importlib.util.module_from_spec(_spec800)
_spec800.loader.exec_module(vk800)


def test_linux_job_is_capped_at_the_core_budget_and_never_fails_the_release():
    job = vk800._jobs()["vulkan-pack-linux"]
    assert job["timeout-minutes"] == vk800._release_check().CORE_JOB_BUDGET_MIN
    assert job["continue-on-error"] is True
    rel = vk800._jobs()["release"]
    assert "vulkan-pack-linux" in rel["needs"] and "needs.vulkan-pack-linux" not in rel["if"]
    dl = [s for s in rel["steps"] if s.get("with", {}).get("name") == "vulkan-pack-linux"]
    assert len(dl) == 1 and dl[0]["continue-on-error"] is True  # no artifact = released without it


@pytest.mark.skipif(vk800.BASH is None, reason="bash is needed to run the step")
def test_release_lists_and_attaches_the_linux_pack_next_to_the_windows_one(tmp_path):
    dist = vk800._release_dir(tmp_path, True)
    (dist / "tlwu-vulkan-pack-linux-0.3.5.zip").write_bytes(b"vk-linux")
    r = vk800.subprocess.run([vk800.BASH, "-e", "-o", "pipefail", "-c", vk800._step("release", "Build install assets")],
                             cwd=tmp_path, capture_output=True, text=True, env={**vk800.os.environ, "TAG": "v0.3.5"})
    assert r.returncode == 0, r.stderr
    sums = (dist / "SHA256SUMS.txt").read_text()
    assert f"{hashlib.sha256(b'vk-linux').hexdigest()}  tlwu-vulkan-pack-linux-0.3.5.zip" in sums
    assert "tlwu-vulkan-pack-0.3.5.zip" in sums
    # what `tlwu-setup --vulkan-pack` on Linux reads from that file
    assert selfupdate.parse_sums(sums, "tlwu-vulkan-pack-linux-0.3.5.zip") == hashlib.sha256(b"vk-linux").hexdigest()


@pytest.mark.skipif(vk800.BASH is None, reason="bash is needed to run the step")
def test_retry_with_only_the_windows_pack_removes_the_stale_linux_pack(tmp_path):
    """A retried draft keeps no pack that is missing from the new SHA256SUMS.txt (CodeRabbit on #1243)."""
    calls = vk800._draft_step(tmp_path, True, "true", ["tlwu-vulkan-pack-0.3.5.zip", "tlwu-vulkan-pack-linux-0.3.5.zip"])
    assert "gh release delete-asset v0.3.5 tlwu-vulkan-pack-linux-0.3.5.zip -y" in calls
    assert not any("delete-asset v0.3.5 tlwu-vulkan-pack-0.3.5.zip" in c for c in calls)
    assert "dist/tlwu-vulkan-pack-0.3.5.zip" in next(c for c in calls if c.startswith("gh release upload"))
