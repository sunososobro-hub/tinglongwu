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
