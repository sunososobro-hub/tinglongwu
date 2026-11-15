"""Entry point of tlwu.exe.

  tlwu.exe                      double-click: first-run setup if needed, then the GUI
  tlwu.exe --cpu                same, forcing the CPU path
  tlwu.exe --setup              open the model setup screen again
  tlwu.exe --smoke              clean-machine check: load every native library, no models, exit 1 on failure
  tlwu.exe --run-selfupdate <a>  auto-update child (network; tinglongwu_setup.selfupdate, never tinglongwu) #768
  tlwu.exe setup <args>        tlwu-setup on the command line (network; e.g. setup --whisper small --langs ru --to zh)
  tlwu.exe speak-smoke <args>  #974 one 我來說 press without the window (tinglongwu/speaksmoke.py; CI)
  tlwu.exe <any tlwu flag>      the normal command line (e.g. --file x.mp3 --to zh, --mic, --gui)
"""
from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path

from . import tiers


LAUNCHER_EXE = "tlwu.exe"  # packaging/launcher/tlwu_launcher.c: sys.executable is this exe in the core layout (#923)


def is_bundled() -> bool:
    """#1007: the launcher's SetEnvironmentVariableW(TLWU_BUNDLED) does not reach os.environ (the C runtime copied the
    environment before it), so the core layout is also recognised by sys.executable being tlwu.exe."""
    return (bool(getattr(sys, "frozen", False)) or "__compiled__" in globals() or os.environ.get("TLWU_BUNDLED") == "1"
            or Path(sys.executable).name.lower() == LAUNCHER_EXE)


def hide_own_console() -> None:
    """Double-clicked console exe: Windows made a console just for us; hide it (the GUI is the interface)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        k = ctypes.windll.kernel32
        pids = (ctypes.c_uint * 4)()
        if k.GetConsoleProcessList(pids, 4) <= 1:  # only this process uses the console
            hwnd = k.GetConsoleWindow()
            if hwnd:
                ctypes.windll.user32.ShowWindow(hwnd, 0)
    except Exception:  # noqa: BLE001
        pass


def prepare_env(bundled: bool, cpu: bool) -> Path:
    """Env set-up before any model library is imported. Returns the models dir."""
    if bundled:
        os.environ["TLWU_BUNDLED"] = "1"
        os.environ.setdefault("TLWU_MODELS", str(tiers.default_models_dir()))
    if cpu:
        os.environ["CUDA_VISIBLE_DEVICES"] = "-1"  # ctranslate2 then reports 0 CUDA devices
    return Path(os.environ.get("TLWU_MODELS") or Path(__file__).resolve().parent.parent / "models")


def split_cpu(argv: list[str]) -> tuple[list[str], bool]:
    return [a for a in argv if a != "--cpu"], "--cpu" in argv


VERSION_RE = re.compile(r'^__version__\s*=\s*["\']([^"\']+)["\']', re.M)
VERSION_CHILD_TIMEOUT_S = 60


def app_version(run=subprocess.run) -> str | None:
    """tinglongwu.__version__ without importing tinglongwu in this process (its offline guard would block the setup
    download, #801). Source: read tinglongwu/__init__.py. Bundled (no .py sources): ask `tlwu.exe --version` in a
    child, which prints "tinglongwu X.Y.Z; ...". None when neither works."""
    spec = importlib.util.find_spec("tinglongwu")
    f = Path(spec.origin) if spec is not None and spec.origin else None
    if f is not None and f.suffix == ".py" and f.is_file():
        m = VERSION_RE.search(f.read_text("utf-8", "replace"))
        if m:
            return m.group(1)
    if not is_bundled():
        return None
    try:
        r = run([sys.executable, "--version"], capture_output=True, text=True, timeout=VERSION_CHILD_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"\btinglongwu (\d+\.\d+\.\d+)\b", (r.stdout or "") + (r.stderr or ""))
    return m.group(1) if m else None


def run_setup_worker(args: list[str]) -> int:
    """Networked child process: only tinglongwu_setup is imported here, never tinglongwu."""
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    # #801 --vulkan-pack and #771 `tlwu.exe setup --default-listen`: tinglongwu_setup.cli knows them, downloader.main does not
    if "--vulkan-pack" in args or "--gpu-pack" in args or "--default-listen" in args or "--talk" in args:  # #1007: --talk = in-app voice install
        from tinglongwu_setup import cli

        if ("--vulkan-pack" in args or "--gpu-pack" in args) and "--app-version" not in args:  # #801: the pack must match this app's version
            v = app_version()
            if v:
                args = [*args, "--app-version", v]

        return cli.main(args)
    from tinglongwu_setup import downloader

    return downloader.main(args)


def run_selfupdate_worker(args: list[str]) -> int:
    """Networked auto-update child (#768): only tinglongwu_setup is imported here, never tinglongwu."""
    from tinglongwu_setup import selfupdate

    return selfupdate.main(args)


def fix_gui_settings(mdir: Path, cpu_tier: bool) -> None:
    """Make the saved GUI model one that is installed; first run on CPU: live draft off (it doubles the work)."""
    from tinglongwu.gui import core

    fresh = not core.settings_path().exists()
    s = core.load_settings()
    best = tiers.best_installed_model(mdir, s["model"])
    changed = False
    if best and best != s["model"]:
        s["model"], changed = best, True
    if fresh and cpu_tier and s["live_draft"]:
        s["live_draft"], changed = False, True
    if changed or fresh:
        core.save_settings(s)


def run_gui(bundled: bool, mdir: Path, cpu: bool, force_setup: bool) -> int:
    if force_setup or not tiers.models_ready(mdir):
        from .setup_gui import run_setup_window

        if not run_setup_window(mdir, bundled, cpu):
            return 0
    gpu_ok = (not cpu) and tiers.detect_gpu() is not None
    try:
        fix_gui_settings(mdir, cpu_tier=not gpu_ok)
    except Exception:  # noqa: BLE001 - never block the GUI on a settings problem
        pass
    from tinglongwu.gui import main as gui_main

    return gui_main()


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["--run-setup"]:
        return run_setup_worker(argv[1:])
    if argv[:1] == ["--run-selfupdate"]:
        return run_selfupdate_worker(argv[1:])
    if argv[:1] == ["--smoke"]:  # clean-machine check: import every native module, no models (#9)
        from .smoke import check

        return check()
    bundled = is_bundled()
    if argv[:1] == ["setup"]:  # terminal users: tlwu.exe setup --whisper small ...
        mdir = prepare_env(bundled, False)
        rest = argv[1:]
        return run_setup_worker(rest if "--models-dir" in rest else rest + ["--models-dir", str(mdir)])
    argv, cpu = split_cpu(argv)
    mdir = prepare_env(bundled, cpu)
    if argv[:1] == ["speak-smoke"]:  # #974: one GUI speak press without the window (install-clean.yml speak-smoke)
        from tinglongwu.speaksmoke import main as smoke_main

        return smoke_main(argv[1:])
    if not argv or argv in (["--gui"], ["--setup"]):
        if bundled:
            hide_own_console()
        return run_gui(bundled, mdir, cpu, argv == ["--setup"])
    if cpu and "--device" not in argv:
        argv += ["--device", "cpu"]
    from tinglongwu.cli import main as cli_main

    return cli_main(argv)
