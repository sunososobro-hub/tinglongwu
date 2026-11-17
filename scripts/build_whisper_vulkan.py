"""Build the whisper.cpp Vulkan ASR library for the Vulkan pack (#783, part 2 of #747).

whisper.cpp publishes no Vulkan binaries, so the release builds it: the pinned commit below, GGML_VULKAN=on, linked
statically into one `tlwu-whisper` library with the flat C API of scripts/whisper_vulkan/tlwu_whisper.cpp (the ctypes
side is tinglongwu.asr_vulkan). `make_vulkan_pack.py --add-whisper` then puts it in the pack zip under `whisper/`.

  python scripts/build_whisper_vulkan.py --out dist/whisper [--cpu]   clone the pin, build, copy lib + LICENSE
  python scripts/build_whisper_vulkan.py --smoke dist/whisper          load it through asr_vulkan and transcribe 1 s
      of silence with whisper.cpp's own test model (models/for-tests-ggml-tiny.bin of the pin); exit 1 on failure
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

REPO = "https://github.com/ggml-org/whisper.cpp"
# tag v1.8.7 (lightweight tag -> this commit), `git ls-remote https://github.com/ggml-org/whisper.cpp refs/tags/v1.8.7`
WHISPER_COMMIT = "48f628a84833905ee4a0658ee6d4a5c915ce1997"
SHIM_DIR = ROOT / "scripts" / "whisper_vulkan"
TEST_MODEL = "models/for-tests-ggml-tiny.bin"


def checkout(dest: Path, run=subprocess.run) -> Path:
    """Shallow fetch of exactly WHISPER_COMMIT (never a branch head) into dest."""
    dest.mkdir(parents=True, exist_ok=True)
    for cmd in (["git", "init", "-q"], ["git", "fetch", "-q", "--depth", "1", REPO, WHISPER_COMMIT],
                ["git", "checkout", "-q", "FETCH_HEAD"]):
        run(cmd, cwd=dest, check=True)
    head = run(["git", "rev-parse", "HEAD"], cwd=dest, check=True, capture_output=True, text=True).stdout.strip()
    if head != WHISPER_COMMIT:
        raise SystemExit(f"whisper.cpp checkout is {head}, pin is {WHISPER_COMMIT}")
    return dest


def build(src: Path, work: Path, out: Path, vulkan: bool = True, run=subprocess.run) -> Path:
    """Configure + build the tlwu-whisper target; copy the library, whisper.cpp's LICENSE and the test model to out."""
    bld = work / "build"
    run(["cmake", "-S", str(SHIM_DIR), "-B", str(bld), f"-DWHISPER_SRC={src}", "-DCMAKE_BUILD_TYPE=Release",
         f"-DGGML_VULKAN={'ON' if vulkan else 'OFF'}"], check=True)
    run(["cmake", "--build", str(bld), "--config", "Release", "--target", "tlwu-whisper", "--parallel"], check=True)
    from tinglongwu.asr_vulkan import WHISPER_LIB

    libs = [p for p in bld.rglob(WHISPER_LIB) if p.is_file()]
    if not libs:
        raise SystemExit(f"build produced no {WHISPER_LIB} under {bld}")
    out.mkdir(parents=True, exist_ok=True)
    shutil.copy2(libs[0], out / WHISPER_LIB)
    shutil.copy2(src / "LICENSE", out / "LICENSE-whisper.cpp.txt")
    (out / "whisper-cpp-commit.txt").write_text(WHISPER_COMMIT + "\n", encoding="utf-8")
    return out / WHISPER_LIB


def smoke(lib_dir: Path, model: Path, out=print) -> int:
    """Load lib_dir/<WHISPER_LIB> through the app's loader and run 1 s of silence; 0 = the C API round-trips."""
    import numpy as np

    from tinglongwu import asr_vulkan

    try:
        backend = asr_vulkan.WhisperCppBackend(Path(lib_dir) / asr_vulkan.WHISPER_LIB, Path(model), use_gpu=True)
        r = backend.transcribe(np.zeros(16000, dtype=np.float32), "en")
    except Exception as e:  # noqa: BLE001 - report any load / ABI failure as one line
        out(f"whisper smoke: FAIL {type(e).__name__}: {e}")
        return 1
    out(f"whisper smoke: ok abi={asr_vulkan.WHISPER_ABI} result={r!r}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", help="folder for the built library")
    ap.add_argument("--cpu", action="store_true", help="GGML_VULKAN=OFF (a local ABI check without the Vulkan SDK)")
    ap.add_argument("--src", help="an existing whisper.cpp checkout at the pin (default: clone it)")
    ap.add_argument("--smoke", metavar="DIR", help="load DIR/<lib> and transcribe silence with the pin's test model")
    ap.add_argument("--model", help="ggml model for --smoke (default: the checkout's " + TEST_MODEL + ")")
    a = ap.parse_args(argv)
    with tempfile.TemporaryDirectory() as td:
        src = Path(a.src) if a.src else None
        if a.out:
            src = src or checkout(Path(td) / "whisper.cpp")
            print(build(src, Path(td), Path(a.out), vulkan=not a.cpu))
        if a.smoke:
            model = Path(a.model) if a.model else (src or checkout(Path(td) / "whisper.cpp")) / TEST_MODEL
            return smoke(Path(a.smoke), model)
    if not (a.out or a.smoke):
        ap.error("--out or --smoke is required")
    return 0


if __name__ == "__main__":
    sys.exit(main())
