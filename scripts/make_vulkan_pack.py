"""Build the release's Vulkan llama.cpp pack zip (#800, part 1 of #797).

A llama-cpp-python wheel compiled with GGML_VULKAN=on keeps its libraries in llama_cpp/lib. This script packs those
DLLs flat into `tlwu-vulkan-pack-<version>.zip` (+ a small manifest), the layout `tinglongwu.mt_backend.vulkan_pack_dir()`
accepts once the zip is unpacked into `vulkan-pack/` (it needs llama.dll at the top level; ggml-vulkan.dll is the
Vulkan backend). Installing the pack is part 2 (#797), measuring on a real GPU part 3.

  python scripts/make_vulkan_pack.py --lib <site-packages>/llama_cpp/lib --version 0.3.5 --out dist
  python scripts/make_vulkan_pack.py --check dist/tlwu-vulkan-pack-0.3.5.zip     list the layout, exit 1 if not accepted
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PACK_NAME = "tlwu-vulkan-pack-{version}.zip"
MANIFEST_NAME = "vulkan-pack.json"
LLAMA_DLL = "llama.dll"              # the library mt_backend.vulkan_pack_dir() looks for at the top level
VULKAN_DLL = "ggml-vulkan.dll"       # present only in a GGML_VULKAN=on build; a CPU-only wheel lacks it
REQUIRED_DLLS = (LLAMA_DLL, VULKAN_DLL)
BYTES_PER_MB = 1 << 20
PIN_FILE = "requirements-llama-cpu.txt"  # the CPU and CUDA builds use the same llama-cpp-python pin


def llama_pin(repo: Path = ROOT) -> str:
    """The llama-cpp-python version pinned for the CPU build (the CUDA file carries the same one)."""
    for line in (repo / PIN_FILE).read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("llama-cpp-python=="):
            return line.split("==", 1)[1].split("#")[0].strip()
    raise SystemExit(f"vulkan pack: no llama-cpp-python== pin in {PIN_FILE}")


def build(lib_dir: Path, out_dir: Path, version: str) -> Path:
    """Zip every DLL of llama_cpp/lib (flat) + the manifest into out_dir/tlwu-vulkan-pack-<version>.zip."""
    lib_dir = Path(lib_dir)
    dlls = sorted(p for p in lib_dir.glob("*.dll") if p.is_file())
    have = {p.name for p in dlls}
    for need in REQUIRED_DLLS:
        if need not in have:
            raise SystemExit(f"vulkan pack: {need} not found in {lib_dir} (build llama-cpp-python with GGML_VULKAN=on)")
    manifest = {"backend": "vulkan", "version": version, "llama_cpp_python": llama_pin(),
                "files": [p.name for p in dlls]}
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    zpath = out_dir / PACK_NAME.format(version=version)
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for p in dlls:
            z.write(p, p.name)
        z.writestr(MANIFEST_NAME, json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return zpath


def layout(zpath: Path) -> list[str]:
    """Sorted member names of the zip (what the release job prints)."""
    with zipfile.ZipFile(zpath) as z:
        return sorted(z.namelist())


def accepted(zpath: Path) -> bool:
    """Unzip into a temp folder and ask mt_backend.vulkan_pack_dir() (the app's own check) about it."""
    from tinglongwu import mt_backend

    with tempfile.TemporaryDirectory() as td, zipfile.ZipFile(zpath) as z:
        z.extractall(td)
        d = Path(td)
        return mt_backend.vulkan_pack_dir(d) == d.resolve() and (d / VULKAN_DLL).is_file()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--lib", help="llama_cpp/lib folder of a GGML_VULKAN=on build")
    ap.add_argument("--version", help="release version without the v (zip name)")
    ap.add_argument("--out", default="dist", help="output folder")
    ap.add_argument("--check", metavar="ZIP", help="list the zip layout and verify vulkan_pack_dir() accepts it")
    a = ap.parse_args(argv)
    if a.check:
        for n in layout(Path(a.check)):
            print(n)
        ok = accepted(Path(a.check))
        print("vulkan_pack_dir: " + ("accepted" if ok else "NOT accepted"))
        return 0 if ok else 1
    if not (a.lib and a.version):
        ap.error("--lib and --version are required (or use --check ZIP)")
    z = build(Path(a.lib), Path(a.out), a.version)
    print(f"{z} ({z.stat().st_size / BYTES_PER_MB:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
