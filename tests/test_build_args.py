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
