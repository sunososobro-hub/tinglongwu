"""#801: `tlwu-setup --vulkan-pack` installs the release's Vulkan llama.cpp pack into vulkan-pack/ (sha256-checked), and a
Vulkan init failure still ends on the CPU with a loaded model."""
from __future__ import annotations

import hashlib
import io
import json
import sys
import types
import urllib.error
import zipfile
from pathlib import Path

import pytest

from tinglongwu import __version__, mt_backend
from tinglongwu_setup import cli, resume, selfupdate, vulkan_pack
from tlwu_app import main as app_main

VERSION = __version__  # #909/#915: mt_backend only accepts a pack installed for the running app version
ZIP_NAME = f"tlwu-vulkan-pack-{VERSION}.zip"
ZIP_URL = f"https://example.invalid/{ZIP_NAME}"
SUMS_URL = "https://example.invalid/SHA256SUMS.txt"
LAYOUT = {"llama.dll": b"llama", "ggml-vulkan.dll": b"vk", "ggml.dll": b"g"}  # what part 1 puts at the zip top


def make_zip(path: Path, files=None) -> bytes:
    with zipfile.ZipFile(path, "w") as z:
        for n, b in (LAYOUT if files is None else files).items():
            z.writestr(n, b)
    return path.read_bytes()


class Release:
    """A fake GitHub release: `get(url, timeout)` for the API / SHA256SUMS.txt, and an opener for resume.fetch."""

    def __init__(self, zip_bytes: bytes | None, sums_sha: str | None = "auto", with_sums_asset=True, prerelease=False):
        assets = []
        if zip_bytes is not None:
            assets.append({"name": ZIP_NAME, "browser_download_url": ZIP_URL})
        if with_sums_asset:
            assets.append({"name": selfupdate.SUMS_NAME, "browser_download_url": SUMS_URL})
        self.rel = {"tag_name": f"v{VERSION}", "prerelease": prerelease, "draft": False, "assets": assets}
        self.zip_bytes = zip_bytes
        sha = hashlib.sha256(zip_bytes or b"").hexdigest() if sums_sha == "auto" else sums_sha
        self.sums = "" if sha is None else f"{sha}  {ZIP_NAME}\n"
        self.fetched = []
        self.asked = []

    def get(self, url, timeout=0):
        self.asked.append(url)
        assert url != selfupdate.API_LATEST, "the pack must come from the app's own release, never latest (#801 A1)"
        if url == selfupdate.API_TAG.format(version=VERSION):
            return json.dumps(self.rel).encode()
        if url.startswith("https://api.github.com/"):  # any other tag: not released
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        assert url == SUMS_URL, url
        return self.sums.encode()

    def opener(self, url, start):
        self.fetched.append(url)
        r = io.BytesIO(self.zip_bytes[start:])
        r.status = 206 if start else 200
        r.headers = {"Content-Length": str(len(self.zip_bytes) - start)}
        return r

    def run(self, dest, lines=None, version=VERSION, env=None):
        out = [] if lines is None else lines
        rc = vulkan_pack.run(dest, version, get=self.get, out=out.append, env={} if env is None else env,
                             fetch=lambda url, d, sha: resume.fetch(url, d, sha, opener=self.opener))
        return rc, out


def listing(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.iterdir())


def test_installs_the_release_zip_and_mt_backend_finds_it(tmp_path):
    app = tmp_path / "app"
    app.mkdir()
    rel = Release(make_zip(tmp_path / "z.zip"))
    rc, out = rel.run(app / "vulkan-pack")
    assert rc == 0 and rel.fetched == [ZIP_URL]
    assert out[-1] == f"vulkan pack {VERSION}: installed in {app / 'vulkan-pack'}"
    assert mt_backend.vulkan_pack_dir(app / "vulkan-pack") == (app / "vulkan-pack").resolve()
    assert (app / "vulkan-pack" / "ggml-vulkan.dll").read_bytes() == b"vk"
    assert listing(app) == ["vulkan-pack"]  # no temp folder, no .part left next to it


def test_default_place_is_vulkan_pack_next_to_the_app_and_the_env_moves_it(tmp_path, monkeypatch):
    monkeypatch.delenv("TLWU_VULKAN_PACK", raising=False)
    assert vulkan_pack.default_dest(tmp_path) == tmp_path / "vulkan-pack"
    assert vulkan_pack.PACK_DIR_NAME == mt_backend.VULKAN_PACK_NAME
    monkeypatch.setenv("TLWU_VULKAN_PACK", str(tmp_path / "elsewhere"))
    assert vulkan_pack.default_dest(tmp_path) == tmp_path / "elsewhere"


def test_reinstall_replaces_the_older_pack(tmp_path):
    dest = tmp_path / "vulkan-pack"
    Release(make_zip(tmp_path / "a.zip", {"llama.dll": b"old", "stale.dll": b"x"})).run(dest)
    Release(make_zip(tmp_path / "b.zip", {"llama.dll": b"new"})).run(dest)
    assert listing(dest) == ["llama.dll", "version.txt"] and (dest / "llama.dll").read_bytes() == b"new"
    assert listing(tmp_path) == ["a.zip", "b.zip", "vulkan-pack"]


def test_sha256_mismatch_is_rejected_and_nothing_is_left_behind(tmp_path):
    app = tmp_path / "app"
    app.mkdir()
    rel = Release(make_zip(tmp_path / "z.zip"), sums_sha="0" * 64)
    rc, out = rel.run(app / "vulkan-pack")
    assert rc == 1 and "FAIL" in out[-1] and "SHA256 mismatch" in out[-1]
    assert listing(app) == []  # no vulkan-pack, no .part, no temp folder
    assert mt_backend.vulkan_pack_dir(app / "vulkan-pack") is None


def test_install_zip_itself_verifies_the_sha256_before_unpacking(tmp_path):
    z = tmp_path / "z.zip"
    make_zip(z)
    with pytest.raises(vulkan_pack.PackError, match="sha256"):
        vulkan_pack.install_zip(z, "0" * 64, tmp_path / "vulkan-pack")
    assert listing(tmp_path) == ["z.zip"]  # nothing unpacked, no temp folder


def test_a_failed_download_keeps_the_pack_that_is_already_installed(tmp_path):
    dest = tmp_path / "vulkan-pack"
    Release(make_zip(tmp_path / "a.zip", {"llama.dll": b"working"})).run(dest)
    rc, _ = Release(make_zip(tmp_path / "b.zip"), sums_sha="f" * 64).run(dest)
    assert rc == 1 and (dest / "llama.dll").read_bytes() == b"working" and listing(tmp_path) == ["a.zip", "b.zip", "vulkan-pack"]


def test_release_without_a_vulkan_zip_prints_one_note_and_exits_0(tmp_path):
    app = tmp_path / "app"
    app.mkdir()
    rel = Release(None)
    rc, out = rel.run(app / "vulkan-pack")
    assert rc == 0 and out == [vulkan_pack.NO_PACK_NOTE] and "no Vulkan pack" in out[0]
    assert rel.fetched == [] and listing(app) == []


def test_prerelease_is_ignored_like_no_pack(tmp_path):
    rc, out = Release(make_zip(tmp_path / "z.zip"), prerelease=True).run(tmp_path / "vulkan-pack")
    assert rc == 0 and out == [vulkan_pack.NO_PACK_NOTE]


@pytest.mark.parametrize("kw", [{"sums_sha": None}, {"with_sums_asset": False}])
def test_zip_not_covered_by_sha256sums_is_never_installed(tmp_path, kw):
    rel = Release(make_zip(tmp_path / "z.zip"), **kw)
    rc, out = rel.run(tmp_path / "vulkan-pack")
    assert rc == 1 and "FAIL" in out[-1] and rel.fetched == [] and not (tmp_path / "vulkan-pack").exists()


def test_zip_slip_and_a_zip_without_llama_library_are_rejected(tmp_path):
    for files, why in (({"../evil.dll": b"x", "llama.dll": b"x"}, "unsafe"), ({"readme.txt": b"x"}, "no llama library")):
        dest = tmp_path / "vulkan-pack"
        rc, out = Release(make_zip(tmp_path / "z.zip", files)).run(dest)
        assert rc == 1 and why in out[-1] and not dest.exists()
        assert not (tmp_path / "evil.dll").exists()


def test_network_error_is_a_failure_not_a_traceback(tmp_path):
    def boom(url, timeout=0):
        raise OSError("no route to host")

    out = []
    assert vulkan_pack.run(tmp_path / "vulkan-pack", VERSION, get=boom, out=out.append, env={}) == 1
    assert out == ["vulkan pack: FAIL OSError: no route to host"]


# ---------------------------------------------------------------- the command line
def test_cli_flag_installs_into_vulkan_pack_next_to_the_app(tmp_path, monkeypatch):
    seen = []
    monkeypatch.delenv("TLWU_VULKAN_PACK", raising=False)
    monkeypatch.delenv("TLWU_OFFLINE", raising=False)
    monkeypatch.setattr(vulkan_pack, "run", lambda dest, version: seen.append((dest, version)) or 0)
    assert cli.main(["--vulkan-pack"]) == 0  # source checkout: the version is read from tinglongwu/__init__.py
    import tinglongwu

    assert seen == [(cli.ROOT / "vulkan-pack", tinglongwu.__version__)]
    assert cli.main(["--vulkan-pack", "--app-version", "0.3.1"]) == 0  # tlwu.exe passes its own version
    assert seen[-1] == (cli.ROOT / "vulkan-pack", "0.3.1")


def test_offline_mode_refuses_the_download(monkeypatch):
    monkeypatch.setenv("TLWU_OFFLINE", "1")
    monkeypatch.setattr(vulkan_pack, "run", lambda dest, version: pytest.fail("network call with TLWU_OFFLINE=1"))
    assert cli.main(["--vulkan-pack"]) == 2


def test_tlwu_exe_setup_routes_the_flag_to_the_setup_cli(monkeypatch):
    seen = []
    monkeypatch.setattr(cli, "main", lambda args: seen.append(args) or 0)
    monkeypatch.setattr(app_main, "app_version", lambda: "0.3.7")
    assert app_main.run_setup_worker(["--vulkan-pack"]) == 0
    assert seen == [["--vulkan-pack", "--app-version", "0.3.7"]]
    assert app_main.run_setup_worker(["--vulkan-pack", "--app-version", "0.3.2"]) == 0  # an explicit one wins
    assert seen[-1] == ["--vulkan-pack", "--app-version", "0.3.2"]


# ---------------------------------------------------------------- #801 Opus A1: the app's own version, never latest
def test_the_installed_version_asks_for_its_own_tag_and_never_latest(tmp_path):
    rel = Release(make_zip(tmp_path / "z.zip"))
    rc, _ = rel.run(tmp_path / "vulkan-pack")
    assert rc == 0 and rel.asked[0] == f"https://api.github.com/repos/{selfupdate.REPO}/releases/tags/v{VERSION}"
    assert selfupdate.API_LATEST not in rel.asked
    assert (tmp_path / "vulkan-pack" / "version.txt").read_text("utf-8").strip() == VERSION


def test_an_app_without_a_release_of_its_version_gets_the_note_and_exit_0(tmp_path):
    rel = Release(make_zip(tmp_path / "z.zip"))  # only v{VERSION} is released
    rc, out = rel.run(tmp_path / "vulkan-pack", version="0.0.1")
    assert rc == 0 and out == [vulkan_pack.NO_PACK_NOTE] and rel.fetched == []
    assert not (tmp_path / "vulkan-pack").exists()


@pytest.mark.parametrize("version", [None, "", "dev"])
def test_unknown_app_version_fails_and_never_falls_back_to_latest(tmp_path, version):
    rel = Release(make_zip(tmp_path / "z.zip"))
    rc, out = rel.run(tmp_path / "vulkan-pack", version=version)
    assert rc == 1 and "FAIL" in out[-1] and "version" in out[-1]
    assert rel.asked == [] and not (tmp_path / "vulkan-pack").exists()


def test_run_refuses_under_tlwu_offline(tmp_path):
    rel = Release(make_zip(tmp_path / "z.zip"))
    rc, out = rel.run(tmp_path / "vulkan-pack", env={"TLWU_OFFLINE": "1"})
    assert rc == 1 and out == ["vulkan pack: FAIL offline (TLWU_OFFLINE=1)"] and rel.asked == []


def test_a_server_error_on_the_tag_is_a_failure_not_the_no_pack_note(tmp_path):
    def five_hundred(url, timeout=0):
        raise urllib.error.HTTPError(url, 500, "Server Error", {}, None)

    out = []
    assert vulkan_pack.run(tmp_path / "vulkan-pack", VERSION, get=five_hundred, out=out.append, env={}) == 1
    assert out[-1].startswith("vulkan pack: FAIL HTTPError")


def test_bundled_app_version_comes_from_a_child_not_an_import(monkeypatch):
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return types.SimpleNamespace(stdout="tinglongwu 0.3.6; third-party model licenses: THIRD_PARTY_NOTICES.md\n",
                                     stderr="")

    monkeypatch.setattr(app_main.importlib.util, "find_spec", lambda name: None)  # compiled: no .py source
    monkeypatch.setattr(app_main, "is_bundled", lambda: True)
    assert app_main.app_version(run=fake_run) == "0.3.6"
    assert calls == [[sys.executable, "--version"]]
    monkeypatch.setattr(app_main, "is_bundled", lambda: False)
    assert app_main.app_version(run=fake_run) is None and len(calls) == 1


def test_source_app_version_reads_the_file(monkeypatch):
    import tinglongwu

    assert app_main.app_version(run=lambda *a, **k: pytest.fail("no child for a source checkout")) == tinglongwu.__version__


# ---------------------------------------------------------------- a Vulkan init failure ends on the CPU
def test_vulkan_init_failure_with_the_pack_path_set_ends_on_cpu_with_a_loaded_model(monkeypatch, tmp_path):
    import os

    from tinglongwu import engines, llamacheck

    pack = tmp_path / "vulkan-pack"
    pack.mkdir()
    (pack / "llama.dll").write_bytes(b"x")
    monkeypatch.setattr(mt_backend, "vulkan_pack_dir", lambda base=None: pack.resolve())
    monkeypatch.setattr(mt_backend, "vulkan_device_present", lambda *a: True)
    monkeypatch.delenv("LLAMA_CPP_LIB_PATH", raising=False)
    monkeypatch.delenv("TLWU_DEVICE", raising=False)
    monkeypatch.setattr(mt_backend, "last_backend", "")
    layers, lib_paths = [], []

    class FakeLlama:
        def __init__(self, **kw):
            layers.append(kw["n_gpu_layers"])
            if kw["n_gpu_layers"] == -1:  # the Vulkan offload: vkCreateInstance / out of memory
                raise RuntimeError("ggml_vulkan: no device")
            self.kw = kw

    fake = types.ModuleType("llama_cpp")
    fake.Llama = FakeLlama

    def import_llama():
        lib_paths.append(os.environ.get("LLAMA_CPP_LIB_PATH"))
        sys.modules["llama_cpp"] = fake
        return fake

    monkeypatch.delitem(sys.modules, "llama_cpp", raising=False)
    monkeypatch.setattr(llamacheck, "import_llama", import_llama)
    monkeypatch.setattr(llamacheck, "check_build", lambda m: None)
    try:
        model = engines.load_llama(tmp_path / "m.gguf", gpu=False)
    finally:
        sys.modules.pop("llama_cpp", None)
    assert lib_paths == [str(pack.resolve())]  # the Vulkan build was the one imported
    assert layers == [-1, 0]  # offload failed, one CPU retry
    assert isinstance(model, FakeLlama) and model.kw["n_gpu_layers"] == 0
    assert mt_backend.last_backend == "cpu"
