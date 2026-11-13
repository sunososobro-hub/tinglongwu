"""`tlwu.exe --smoke`: clean-machine check of the packaged app (#9), no models and no network.

On a fresh Windows (no Python, no VC++ redistributable, no CUDA) the usual failure is a native library that
cannot load: a missing DLL shows up as ImportError/OSError when the module is imported. This imports every
native module the exe ships, prints one OK/FAIL line each and exits 1 if a required one fails, so a
Windows Sandbox run (packaging/clean_machine_check.py) can check the exe without anyone clicking through it.

Pure-Python packages that load submodules lazily (module-level __getattr__) are checked too (#310): Nuitka cannot
see those imports, and the first-run download failed with `No module named 'filelock._soft'` only when
huggingface_hub touched filelock.SoftFileLock. LAZY resolves every lazy attribute the download path needs.
"""
from __future__ import annotations

import importlib
import sys
import time

# (module, required). Optional = the app still runs without it (llama_cpp: Hy-MT2 only; ggml-cuda needs a GPU).
MODULES: list[tuple[str, bool]] = [
    ("numpy", True),
    ("scipy.signal", True),
    ("soundfile", True),
    ("soundcard", True),
    ("ctranslate2", True),
    ("sentencepiece", True),
    ("onnxruntime", True),
    ("tokenizers", True),
    ("faster_whisper", True),
    ("opencc", True),
    ("tkinter", True),
    ("sv_ttk", True),
    ("tinglongwu.gui.app", True),
    ("tinglongwu_setup.downloader", True),
    ("tinglongwu.lang.ru", True),  # #390: pymorphy3 + its dictionary (package data) must work in the exe
    ("llama_cpp", False),
]
# Modules the app never imports directly: llama_cpp goes through import_llama (CUDA DLLs + CPU fallback, #398)
IMPORTERS = {"llama_cpp": lambda: importlib.import_module("tinglongwu.llamacheck").import_llama(),
             "tinglongwu.lang.ru": lambda: check_ru_names()}


def check_ru_names(load=None) -> None:
    """#390: the Russian name patch must really run in the package, not silently fall back to pass-through."""
    ru = importlib.import_module("tinglongwu.lang.ru")
    morph = (load or ru.load_analyzer)()  # raises when pymorphy3 or its dictionary is missing
    got = ru.normalize_names("Максима Чижова.", morph=morph)
    if got != "Максим Чижов.":
        raise RuntimeError(f"ru pre_mt gave {got!r}")

# (module, attributes, required). None = every lazy attribute the module declares (filelock: _LAZY_ATTRIBUTES).
LAZY: list[tuple[str, tuple[str, ...] | None, bool]] = [
    ("filelock", None, True),
    ("huggingface_hub", ("HfApi", "snapshot_download", "hf_hub_download"), True),  # tinglongwu_setup.downloader
    ("huggingface_hub.utils", (), True),  # utils/_fixes.py runs `from filelock import ...` on import
]


def lazy_names(mod) -> list[str]:
    """Attributes `mod` resolves on first access: filelock's _LAZY_ATTRIBUTES map, else its __all__."""
    names = getattr(mod, "_LAZY_ATTRIBUTES", None)
    return sorted(names) if isinstance(names, dict) else sorted(getattr(mod, "__all__", ()))


def _line(name, required, t0, err) -> tuple[str, int]:
    if err is None:
        return f"OK   {name} ({time.perf_counter() - t0:.1f}s)", 0
    if isinstance(err, KeyboardInterrupt):
        raise err
    return f"{'FAIL' if required else 'WARN'} {name}: {type(err).__name__}: {err}", int(required)


def check_lazy(lazy=None, out=None) -> int:
    """Import each LAZY module and getattr each attribute; prints OK/FAIL lines, returns the required failures."""
    out = out or sys.stdout
    failed = 0
    for name, attrs, required in (LAZY if lazy is None else lazy):
        t0 = time.perf_counter()
        try:
            mod = importlib.import_module(name)
        except BaseException as e:  # noqa: BLE001
            line, f = _line(name, required, t0, e)
            print(line, file=out, flush=True)
            failed += f
            continue
        print(_line(name, required, t0, None)[0], file=out, flush=True)
        for attr in (lazy_names(mod) if attrs is None else attrs):
            t0, err = time.perf_counter(), None
            try:
                getattr(mod, attr)
            except BaseException as e:  # noqa: BLE001 - AttributeError / ModuleNotFoundError from the lazy import
                err = e
            line, f = _line(f"{name}.{attr}", required, t0, err)
            print(line, file=out, flush=True)
            failed += f
    return failed


def check(modules: list[tuple[str, bool]] | None = None, out=None, lazy=None) -> int:
    out = out or sys.stdout
    failed = 0
    for name, required in (MODULES if modules is None else modules):
        t0 = time.perf_counter()
        try:
            IMPORTERS.get(name, lambda: importlib.import_module(name))()
            line = f"OK   {name} ({time.perf_counter() - t0:.1f}s)"
            if name == "llama_cpp":
                reason = getattr(sys.modules.get("tinglongwu.llamacheck"), "cpu_fallback", None)
                if reason:
                    line += f"\nCPU  llama_cpp: CUDA build not used ({reason}); loaded llama_cpp/lib_cpu"
        except BaseException as e:  # noqa: BLE001 - a broken DLL can raise anything, even SystemExit
            if isinstance(e, KeyboardInterrupt):
                raise
            failed += required
            line = f"{'FAIL' if required else 'WARN'} {name}: {type(e).__name__}: {e}"
        print(line, file=out, flush=True)
    failed += check_lazy(lazy, out)
    print(f"smoke: {'FAIL' if failed else 'OK'} ({failed} required module(s) failed)", file=out, flush=True)
    return 1 if failed else 0
