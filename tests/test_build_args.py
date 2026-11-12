"""#36: lazily-imported packages must be bundled by the Nuitka build (filelock._soft was missing in the exe)."""
import importlib.util
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "packaging" / "build_windows.py"


def test_nuitka_includes_lazy_import_packages():
    spec = importlib.util.spec_from_file_location("build_windows", SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    cmd = mod.nuitka_cmd()
    assert "--include-package=filelock" in cmd
    assert "--include-package=huggingface_hub" in cmd
    assert "--include-package=sv_ttk" in cmd
    assert "--include-package-data=sv_ttk" in cmd  # #77: theme .tcl/png data


def test_voice_modules_and_worker_sources_are_packaged(tmp_path):
    """#464: tlwu --voice (#330) imports tinglongwu.reverse; the Kokoro venv runs tts_worker.py from source."""
    spec = importlib.util.spec_from_file_location("build_windows", SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    cmd = mod.nuitka_cmd()
    assert "--nofollow-import-to=tinglongwu.reverse" not in cmd
    assert "--nofollow-import-to=tinglongwu.reverse.tts_worker" in cmd
    copied = mod.copy_worker_sources(tmp_path)
    assert (tmp_path / "tinglongwu" / "reverse" / "tts_worker.py").is_file()
    for rel in copied:
        assert (tmp_path / rel).read_bytes() == (SRC.parent.parent / rel).read_bytes()
    # every tinglongwu module the worker / offline guard imports at module level or in _emit is shipped
    assert {"tinglongwu/offline.py", "tinglongwu/i18n.py", "tinglongwu/userdirs.py"} <= set(copied)
