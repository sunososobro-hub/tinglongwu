"""#36 / #923: the Windows build ships whole packages (lazily-imported submodules included) and the worker sources."""
import importlib.util
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "packaging" / "build_windows.py"
sys.path.insert(0, str(SRC.parent))
import runtime_layer  # noqa: E402


def _mod():
    spec = importlib.util.spec_from_file_location("build_windows", SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_runtime_layer_ships_whole_packages_with_their_data(tmp_path):
    """#36 filelock._soft, #77 sv_ttk theme data: a package is copied as a folder, nothing is picked by import graph."""
    site, dst = tmp_path / "site", tmp_path / "layer"
    for rel in ("filelock/__init__.py", "filelock/_soft.py", "sv_ttk/__init__.py", "sv_ttk/theme/dark.tcl",
                "sv_ttk-2.6.1.dist-info/METADATA", "pip/__init__.py", "pip-26.3.dist-info/METADATA",
                "tinglongwu/__init__.py", "numpy/tests/test_x.py", "numpy/testing/__init__.py", "x.pth",
                "bin/llama-common.dll", "lib/cmake/llama/llama-config.cmake", "include/llama.h",
                "soundcard/mediafoundation.py.h"):
        f = site / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("x = 1\n")
    dst.mkdir()
    copied = runtime_layer.copy_site_packages(dst, site)
    assert (dst / "filelock" / "_soft.py").is_file() and (dst / "sv_ttk" / "theme" / "dark.tcl").is_file()
    assert (dst / "soundcard" / "mediafoundation.py.h").is_file()  # read by soundcard at import: data stays
    assert (dst / "numpy" / "testing" / "__init__.py").is_file() and not (dst / "numpy" / "tests").exists()
    # build tools, the app layer, .pth files, the llama-cpp-python wheel's CMake install folders
    for gone in ("pip", "pip-26.3.dist-info", "tinglongwu", "x.pth", "bin", "lib", "include"):
        assert gone not in copied and not (dst / gone).exists()
    assert runtime_layer.compile_sourceless(dst) == (4, 0)
    assert (dst / "filelock" / "_soft.pyc").is_file() and not (dst / "filelock" / "_soft.py").exists()


def test_voice_modules_and_worker_sources_are_packaged(tmp_path):
    """#464: tlwu --voice (#330) imports tinglongwu.reverse; the Kokoro venv runs tts_worker.py from source."""
    mod = _mod()
    assert mod.build_app_layer(tmp_path) == list(mod.APP_PACKAGES) == list(runtime_layer.APP_PACKAGES)
    assert (tmp_path / "tinglongwu" / "reverse" / "tts_worker.py").is_file()
    for rel in mod.WORKER_SOURCES:
        assert (tmp_path / rel).read_bytes() == (SRC.parent.parent / rel).read_bytes()
    # every tinglongwu module the worker / offline guard imports at module level or in _emit is shipped
    assert {"tinglongwu/offline.py", "tinglongwu/i18n.py", "tinglongwu/userdirs.py"} <= set(mod.WORKER_SOURCES)
