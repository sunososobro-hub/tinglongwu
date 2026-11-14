"""#800: scripts/make_vulkan_pack.py turns a Vulkan build of llama-cpp-python into the release's vulkan-pack zip,
laid out so tinglongwu.mt_backend.vulkan_pack_dir() accepts the unzipped folder."""
import importlib.util
import json
import zipfile
from pathlib import Path

import pytest

from tinglongwu import mt_backend

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "scripts" / "make_vulkan_pack.py"


def _mod():
    assert SRC.is_file(), "scripts/make_vulkan_pack.py is missing (#800)"
    spec = importlib.util.spec_from_file_location("make_vulkan_pack", SRC)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _lib(root: Path, names=("llama.dll", "ggml.dll", "ggml-base.dll", "ggml-cpu.dll", "ggml-vulkan.dll")) -> Path:
    """Fixture mirroring site-packages/llama_cpp/lib of a GGML_VULKAN=on build."""
    lib = root / "llama_cpp" / "lib"
    lib.mkdir(parents=True)
    for n in names:
        (lib / n).write_bytes(b"dll-" + n.encode())
    (lib / "README.txt").write_text("not a library")
    return lib


def test_zip_unpacks_into_a_folder_vulkan_pack_dir_accepts(tmp_path):
    m = _mod()
    zpath = m.build(_lib(tmp_path), tmp_path / "out", "0.3.5")
    assert zpath.name == "tlwu-vulkan-pack-0.3.5.zip"
    folder = tmp_path / "unzipped"
    with zipfile.ZipFile(zpath) as z:
        z.extractall(folder)
    assert mt_backend.vulkan_pack_dir(folder) == folder.resolve()
    assert (folder / "ggml-vulkan.dll").read_bytes() == b"dll-ggml-vulkan.dll"
    assert (folder / "llama.dll").is_file()


def test_layout_is_flat_dlls_plus_manifest_and_nothing_else(tmp_path):
    m = _mod()
    zpath = m.build(_lib(tmp_path), tmp_path / "out", "0.3.5")
    names = m.layout(zpath)
    assert names == sorted(names)
    assert set(names) == {"llama.dll", "ggml.dll", "ggml-base.dll", "ggml-cpu.dll", "ggml-vulkan.dll", m.MANIFEST_NAME}
    assert all("/" not in n for n in names)  # nothing in a subfolder: vulkan_pack_dir looks at the top level


def test_manifest_carries_the_llama_pin_of_the_cpu_and_cuda_builds(tmp_path):
    m = _mod()
    zpath = m.build(_lib(tmp_path), tmp_path / "out", "0.3.5")
    man = json.loads(zipfile.ZipFile(zpath).read(m.MANIFEST_NAME))
    pins = set()
    for req in ("requirements-llama-cpu.txt", "requirements-llama-cuda.txt"):
        for line in (REPO / req).read_text().splitlines():
            if line.startswith("llama-cpp-python=="):
                pins.add(line.split("==")[1].strip())
    assert pins == {man["llama_cpp_python"]}  # same llama.cpp pin as the CPU / CUDA builds
    assert man["backend"] == "vulkan" and man["version"] == "0.3.5"


@pytest.mark.parametrize("missing", ["ggml-vulkan.dll", "llama.dll"])
def test_a_build_without_the_vulkan_or_llama_dll_is_not_packed(tmp_path, missing):
    m = _mod()
    names = [n for n in ("llama.dll", "ggml.dll", "ggml-cpu.dll", "ggml-vulkan.dll") if n != missing]
    with pytest.raises(SystemExit) as e:
        m.build(_lib(tmp_path, names), tmp_path / "out", "0.3.5")  # e.g. a CPU-only wheel got installed
    assert missing in str(e.value)
    assert not (tmp_path / "out").exists() or not list((tmp_path / "out").glob("*.zip"))


def test_check_accepts_the_built_zip_and_rejects_a_foreign_one(tmp_path, capsys):
    m = _mod()
    good = m.build(_lib(tmp_path), tmp_path / "out", "0.3.5")
    assert m.main(["--check", str(good)]) == 0
    listed = capsys.readouterr().out
    assert "ggml-vulkan.dll" in listed  # the job summary lists the layout
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("llama_cpp/lib/llama.dll", b"x")  # nested: vulkan_pack_dir would not accept it
    assert m.main(["--check", str(bad)]) == 1


# ---- the release.yml wiring: the job's budget and the release job's steps, executed (not grepped)

import hashlib  # noqa: E402
import os  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402

yaml = pytest.importorskip("yaml")
BASH = shutil.which("bash")
WORKFLOW = REPO / ".github" / "workflows" / "release.yml"


def _jobs():
    return yaml.safe_load(WORKFLOW.read_text("utf-8"))["jobs"]


def _release_check():
    spec = importlib.util.spec_from_file_location("release_check_800", REPO / "scripts" / "release_check.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _step(job, name_start):
    return next(s for s in _jobs()[job]["steps"] if s.get("name", "").startswith(name_start))["run"]


def test_vulkan_job_is_capped_at_the_core_budget_and_never_fails_the_release():
    job = _jobs()["vulkan-pack"]
    assert job["timeout-minutes"] == _release_check().CORE_JOB_BUDGET_MIN  # a slow compile only drops the asset
    assert job["continue-on-error"] is True
    assert "vulkan-pack" in _jobs()["release"]["needs"]
    assert job["env"]["VULKAN_SDK_VERSION"]  # the installer is pinned by version, and by hash once measured


def _release_dir(tmp_path, with_pack):
    for name in ("install.sh", "mac_install.sh", "bootstrap_windows.ps1"):
        (tmp_path / "scripts").mkdir(exist_ok=True)
        shutil.copy(REPO / "scripts" / name, tmp_path / "scripts" / name)
    dist = tmp_path / "dist"
    dist.mkdir()
    core = dist / "tinglongwu-win64.zip"
    core.write_bytes(b"core")
    (dist / "tinglongwu-win64.zip.sha256").write_text(f"{hashlib.sha256(b'core').hexdigest()}  tinglongwu-win64.zip\n")
    if with_pack:
        (dist / "tlwu-vulkan-pack-0.3.5.zip").write_bytes(b"vk")
    (dist / "notes.md").write_text("notes")
    return dist


@pytest.mark.skipif(BASH is None, reason="bash is needed to run the step")
@pytest.mark.parametrize("with_pack", [True, False])
def test_sha256sums_lists_the_pack_only_when_it_was_built(tmp_path, with_pack):
    dist = _release_dir(tmp_path, with_pack)
    r = subprocess.run([BASH, "-e", "-o", "pipefail", "-c", _step("release", "Build install assets")], cwd=tmp_path,
                       capture_output=True, text=True, env={**os.environ, "TAG": "v0.3.5"})
    assert r.returncode == 0, r.stderr  # a missing pack is a warning, never a failure
    sums = (dist / "SHA256SUMS.txt").read_text()
    assert ("tlwu-vulkan-pack-0.3.5.zip" in sums) is with_pack
    assert ("no Vulkan pack" in r.stdout) is (not with_pack)
    assert f"{hashlib.sha256(b'vk').hexdigest()}  tlwu-vulkan-pack-0.3.5.zip" in sums or not with_pack


def _draft_step(tmp_path, with_pack, draft, assets):
    """Run the 'Create the draft GitHub Release' step with a recording `gh`; draft = 'none' | 'true' | 'false'."""
    _release_dir(tmp_path, with_pack)
    shim_dir, log = tmp_path / "shim", tmp_path / "gh.log"
    shim_dir.mkdir()
    gh = shim_dir / "gh"
    gh.write_text('#!/bin/bash\n'
                  'echo "gh $*" >> "$GH_LOG"\n'
                  'case "$*" in\n'
                  '  *"--json isDraft"*) [ "$DRAFT" = none ] && exit 1; echo "$DRAFT";;\n'
                  '  *"--json assets"*) printf "%s\\n" $ASSETS;;\n'
                  'esac\n'
                  'exit 0\n')
    gh.chmod(0o755)
    (tmp_path / "dist" / "SHA256SUMS.txt").write_text("sums")
    for n in ("install.sh", "mac_install.sh", "install.ps1"):
        (tmp_path / "dist" / n).write_text("x")
    r = subprocess.run([BASH, "-e", "-o", "pipefail", "-c", _step("release", "Create the draft")], cwd=tmp_path,
                       capture_output=True, text=True,
                       env={**os.environ, "PATH": f"{shim_dir}:{os.environ['PATH']}", "GH_LOG": str(log),
                            "TAG": "v0.3.5", "DRAFT": draft, "ASSETS": " ".join(assets)})
    assert r.returncode == 0, r.stderr
    return log.read_text().splitlines()


@pytest.mark.skipif(BASH is None, reason="bash is needed to run the step")
def test_new_draft_attaches_the_pack_next_to_the_core_zip(tmp_path):
    calls = _draft_step(tmp_path, True, "none", [])
    create = next(c for c in calls if c.startswith("gh release create"))
    assert "dist/tlwu-vulkan-pack-0.3.5.zip" in create and "dist/tinglongwu-win64.zip" in create


@pytest.mark.skipif(BASH is None, reason="bash is needed to run the step")
def test_retried_draft_without_the_pack_removes_the_stale_pack_so_none_lacks_a_checksum(tmp_path):
    calls = _draft_step(tmp_path, False, "true", ["tinglongwu-win64.zip", "tlwu-vulkan-pack-0.3.5.zip"])
    assert "gh release delete-asset v0.3.5 tlwu-vulkan-pack-0.3.5.zip -y" in calls
    upload = next(c for c in calls if c.startswith("gh release upload"))
    assert "tlwu-vulkan-pack" not in upload and "--clobber" in upload
    assert not any("delete-asset v0.3.5 tinglongwu-win64.zip" in c for c in calls)  # only the Vulkan pack goes


@pytest.mark.skipif(BASH is None, reason="bash is needed to run the step")
def test_retried_draft_with_the_pack_replaces_it_and_deletes_nothing(tmp_path):
    calls = _draft_step(tmp_path, True, "true", ["tlwu-vulkan-pack-0.3.5.zip"])
    assert not any("delete-asset" in c for c in calls)
    assert "dist/tlwu-vulkan-pack-0.3.5.zip" in next(c for c in calls if c.startswith("gh release upload"))


@pytest.mark.skipif(BASH is None, reason="bash is needed to run the step")
def test_published_release_is_never_touched(tmp_path):
    _release_dir(tmp_path, False)
    # draft = false -> exit 1 before any upload / delete (asserted through the step's own exit code)
    shim_dir = tmp_path / "shim"
    shim_dir.mkdir()
    gh = shim_dir / "gh"
    gh.write_text('#!/bin/bash\necho "gh $*" >> "$GH_LOG"\ncase "$*" in *"--json isDraft"*) echo false;; esac\nexit 0\n')
    gh.chmod(0o755)
    r = subprocess.run([BASH, "-e", "-o", "pipefail", "-c", _step("release", "Create the draft")], cwd=tmp_path,
                       capture_output=True, text=True,
                       env={**os.environ, "PATH": f"{shim_dir}:{os.environ['PATH']}", "GH_LOG": str(tmp_path / "log"),
                            "TAG": "v0.3.5"})
    assert r.returncode == 1 and "already published" in r.stdout
    assert not any("upload" in c or "delete-asset" in c for c in (tmp_path / "log").read_text().splitlines())
