"""Does the installed llama-cpp-python build match this CPU? (issue #74)

Prebuilt llama-cpp-python wheels are compiled on CI runners with native CPU flags, so the same version can
come out with or without AVX-512 depending on the index variant (0.3.36: cu125 has AVX-512, cu124 / cu130 /
cpu do not). An AVX-512 build on a CPU without AVX-512 imports fine and then dies while loading a model with
STATUS_ILLEGAL_INSTRUCTION (Windows error 0xc000001d; SIGILL on Linux). We compare the flags the build
reports (`llama_print_system_info`) with what the CPU supports before loading, and raise a clear error.
"""
from __future__ import annotations

import logging
import re
import sys

_log = logging.getLogger(__name__)

LLAMA_VERSION = "0.3.36"
INDEX = "https://abetlen.github.io/llama-cpp-python/whl"
FIX_HINT = ("reinstall a build that fits this CPU (needs network, once): "
            f"pip install --force-reinstall --no-deps --only-binary=:all: llama-cpp-python=={LLAMA_VERSION} "
            f"--extra-index-url {INDEX}/cu124   (no NVIDIA GPU: use {INDEX}/cpu)")
ILLEGAL_INSTRUCTION = 0xC000001D  # NTSTATUS STATUS_ILLEGAL_INSTRUCTION

# /proc/cpuinfo flag -> name used by llama_print_system_info
_LINUX_FLAGS = {"pni": "SSE3", "ssse3": "SSSE3", "avx": "AVX", "avx2": "AVX2", "f16c": "F16C", "fma": "FMA",
                "bmi2": "BMI2", "avx_vnni": "AVX_VNNI", "avx512f": "AVX512", "avx512vbmi": "AVX512_VBMI",
                "avx512_vnni": "AVX512_VNNI", "avx512_bf16": "AVX512_BF16"}
# IsProcessorFeaturePresent ids (winnt.h PF_*)
_WIN_PF = {"AVX": 39, "AVX2": 40, "AVX512": 41}


class LlamaBuildError(RuntimeError):
    """The installed llama-cpp-python build cannot run on this machine."""


def build_features(system_info: str) -> set[str]:
    """CPU features a llama.cpp build was compiled with, from `llama_print_system_info()` text."""
    cpu = system_info.split("CPU :", 1)[-1]  # newer builds prefix backend sections ("CUDA : ... | CPU : ...")
    return set(re.findall(r"\b([A-Z][A-Z0-9_]*) = 1\b", cpu))


def cpu_features() -> dict[str, bool] | None:
    """{feature: supported} for the features we can tell on this OS; None when nothing can be determined."""
    if sys.platform == "win32":
        import ctypes

        have = ctypes.windll.kernel32.IsProcessorFeaturePresent
        out = {name: bool(have(pf)) for name, pf in _WIN_PF.items()}
        # Windows builds that predate PF 40/41 answer False for ids they do not know: without a positive
        # AVX2 answer we cannot tell "no AVX-512" from "old Windows", so claim nothing about them.
        return out if out["AVX2"] else {"AVX": out["AVX"]} if out["AVX"] else None
    try:
        with open("/proc/cpuinfo", encoding="utf-8", errors="replace") as f:
            line = next((ln for ln in f if ln.startswith("flags")), "")
    except OSError:
        return None
    flags = set(line.split(":", 1)[-1].split())
    return {name: flag in flags for flag, name in _LINUX_FLAGS.items()} if flags else None


def unsupported(build: set[str], cpu: dict[str, bool] | None) -> list[str]:
    """Features the build needs that the CPU is known not to have (AVX512_* sub-features follow AVX512)."""
    if not cpu:
        return []
    bad = []
    for name in sorted(build):
        key = name if name in cpu else "AVX512" if name.startswith("AVX512") else None
        if key in cpu and not cpu[key]:
            bad.append(name)
    return bad


def check_build(llama_cpp, cpu=None) -> None:
    """Raise LlamaBuildError if the imported llama_cpp module was compiled for instructions this CPU lacks."""
    try:
        info = llama_cpp.llama_print_system_info()
    except Exception:  # noqa: BLE001 - cannot tell: let the real load decide
        return
    if isinstance(info, bytes):
        info = info.decode("utf-8", "replace")
    bad = unsupported(build_features(info or ""), (cpu or cpu_features)())
    if bad:
        raise LlamaBuildError(f"this llama-cpp-python build was compiled for {', '.join(bad)}, which this CPU "
                              f"does not support (it would crash with an illegal instruction). To fix: {FIX_HINT}")


def is_illegal_instruction(e: BaseException) -> bool:
    code = getattr(e, "winerror", None)
    return (isinstance(code, int) and code & 0xFFFFFFFF == ILLEGAL_INSTRUCTION) or "0xc000001d" in str(e).lower()


def explain(e: BaseException) -> BaseException:
    """Turn a raw 0xc000001d OSError from llama.dll into a LlamaBuildError that says what to do."""
    if isinstance(e, LlamaBuildError) or not is_illegal_instruction(e):
        return e
    return LlamaBuildError(f"llama.cpp hit an illegal CPU instruction ({e}): the installed llama-cpp-python "
                           f"build does not match this CPU. To fix: {FIX_HINT}")


cpu_fallback: str | None = None  # set by import_llama when it fell back to llama_cpp/lib_cpu: the CUDA error


def import_llama():
    """`import llama_cpp` with the CUDA DLLs registered. In the standalone build, a CUDA llama.dll that cannot load
    (no NVIDIA driver, CUDA runtime DLLs missing) falls back to the bundled CPU build in llama_cpp/lib_cpu (#398)."""
    import os

    from . import device

    global cpu_fallback
    device.register_cuda_libs()
    device.use_pack_llama_build()
    if device.use_llama_cpu_build():
        cpu_fallback = device.llama_cpu_reason or "CUDA llama build not loadable"
    try:
        import llama_cpp
    except (OSError, RuntimeError) as e:
        if device.llama_backend == "gpu-pack":  # the pack's CUDA build cannot load: use the core's own CPU build
            device.llama_backend = "default"
            cpu_fallback = f"{type(e).__name__}: {e}"
            os.environ.pop("LLAMA_CPP_LIB_PATH", None)
            import llama_cpp
            return llama_cpp
        cpu = device.bundled_llama_cpu_dir()
        if cpu is None or os.environ.get("LLAMA_CPP_LIB_PATH") == str(cpu):
            raise
        cpu_fallback = f"{type(e).__name__}: {e}"
        os.environ["LLAMA_CPP_LIB_PATH"] = str(cpu)  # a failed import leaves nothing in sys.modules: retry once
        import llama_cpp
    if cpu_fallback:  # keep the CUDA error visible: a broken CUDA llama.dll must not hide behind the CPU build
        _log.warning("llama_cpp: CUDA build not used (%s); using the CPU build in %s", cpu_fallback,
                     os.environ.get("LLAMA_CPP_LIB_PATH"))
    return llama_cpp


def self_check(gpu: bool = False, importer=import_llama, cpu=None) -> tuple[bool, str]:
    """Load check without a model, for the installer / `tlwu-setup --check-llama`. Returns (ok, message)."""
    try:
        mod = importer()
    except Exception as e:  # noqa: BLE001 - ImportError, or llama.dll / a CUDA DLL not loadable
        return False, f"llama-cpp-python cannot be loaded ({str(e)[:200]}). To fix: {FIX_HINT}"
    try:
        check_build(mod, cpu)
    except LlamaBuildError as e:
        return False, str(e)
    if gpu and not mod.llama_supports_gpu_offload():
        return False, f"llama-cpp-python is a CPU-only build (no GPU offload). To fix: {FIX_HINT}"
    return True, f"llama-cpp-python {getattr(mod, '__version__', '?')} OK for this CPU"


def main(argv=None) -> int:
    """`python -m tinglongwu.llamacheck [--gpu]` (what `tlwu-setup --check-llama` runs): exit 0 if usable."""
    ok, msg = self_check(gpu="--gpu" in (sys.argv[1:] if argv is None else argv))
    print(f"tlwu: {msg}", file=sys.stdout if ok else sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
