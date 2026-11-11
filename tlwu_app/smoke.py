"""`tlwu.exe --smoke`: clean-machine check of the packaged app (#9), no models and no network.

On a fresh Windows (no Python, no VC++ redistributable, no CUDA) the usual failure is a native library that
cannot load: a missing DLL shows up as ImportError/OSError when the module is imported. This imports every
native module the exe ships, prints one OK/FAIL line each and exits 1 if a required one fails, so a
Windows Sandbox run (packaging/clean_machine_check.py) can check the exe without anyone clicking through it.
"""
from __future__ import annotations

import importlib
import sys
import time

# (module, required). Optional = the app still runs without it (llama_cpp: Qwen only; ggml-cuda needs a GPU).
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
    ("llama_cpp", False),
]


def check(modules: list[tuple[str, bool]] | None = None, out=None) -> int:
    out = out or sys.stdout
    failed = 0
    for name, required in (MODULES if modules is None else modules):
        t0 = time.perf_counter()
        try:
            importlib.import_module(name)
            line = f"OK   {name} ({time.perf_counter() - t0:.1f}s)"
        except BaseException as e:  # noqa: BLE001 - a broken DLL can raise anything, even SystemExit
            if isinstance(e, KeyboardInterrupt):
                raise
            failed += required
            line = f"{'FAIL' if required else 'WARN'} {name}: {type(e).__name__}: {e}"
        print(line, file=out, flush=True)
    print(f"smoke: {'FAIL' if failed else 'OK'} ({failed} required module(s) failed)", file=out, flush=True)
    return 1 if failed else 0
