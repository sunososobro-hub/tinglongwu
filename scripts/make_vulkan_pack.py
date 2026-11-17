"""Build the release's Vulkan llama.cpp pack zip (#800, part 1 of #797).

A llama-cpp-python wheel compiled with GGML_VULKAN=on keeps its libraries in llama_cpp/lib. This script packs those
DLLs flat into `tlwu-vulkan-pack-<version>.zip` (+ a small manifest), the layout `tinglongwu.mt_backend.vulkan_pack_dir()`
accepts once the zip is unpacked into `vulkan-pack/` (it needs llama.dll at the top level; ggml-vulkan.dll is the
Vulkan backend). Installing the pack is part 2 (#797), measuring on a real GPU part 3.
#1198: a Linux build (libllama.so + libggml-vulkan.so) becomes `tlwu-vulkan-pack-linux-<version>.zip`; the platform
is read from the libraries in --lib, so the same command packs either build.

  python scripts/make_vulkan_pack.py --lib <site-packages>/llama_cpp/lib --version 0.3.5 --out dist
  python scripts/make_vulkan_pack.py --check dist/tlwu-vulkan-pack-0.3.5.zip     list the layout, exit 1 if not accepted
  python scripts/make_vulkan_pack.py --smoke dist/tlwu-vulkan-pack-linux-0.3.5.zip
      load the pack's Vulkan backend and list its devices (CI: Mesa lavapipe), exit 1 when it sees none (#1198)
  python scripts/make_vulkan_pack.py --add-whisper dist/tlwu-vulkan-pack-0.3.5.zip --whisper dist/whisper
      #783: add the whisper.cpp Vulkan ASR build (scripts/build_whisper_vulkan.py) under whisper/ in the zip
"""
from __future__ import annotations

import argparse
import ctypes
import json
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PACK_NAME = "tlwu-vulkan-pack-{version}.zip"
MANIFEST_NAME = "vulkan-pack.json"
VERSION_FILE = "version.txt"           # = tinglongwu.mt_backend.VERSION_FILE (#909/#915)
LLAMA_DLL = "llama.dll"              # the library mt_backend.vulkan_pack_dir() looks for at the top level
VULKAN_DLL = "ggml-vulkan.dll"       # present only in a GGML_VULKAN=on build; a CPU-only wheel lacks it
REQUIRED_DLLS = (LLAMA_DLL, VULKAN_DLL)
# #1198: per platform: zip name, library glob, required libraries. "windows" is the #800 pack, names unchanged.
PLATFORMS = {
    "windows": {"zip": PACK_NAME, "glob": "*.dll", "required": REQUIRED_DLLS},
    "linux": {"zip": "tlwu-vulkan-pack-linux-{version}.zip", "glob": "*.so*",
              "required": ("libllama.so", "libggml-vulkan.so")},
}
BYTES_PER_MB = 1 << 20
PIN_FILE = "requirements-llama-cpu.txt"  # the CPU and CUDA builds use the same llama-cpp-python pin


def llama_pin(repo: Path = ROOT) -> str:
    """The llama-cpp-python version pinned for the CPU build (the CUDA file carries the same one)."""
    for line in (repo / PIN_FILE).read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("llama-cpp-python=="):
            return line.split("==", 1)[1].split("#")[0].strip()
    raise SystemExit(f"vulkan pack: no llama-cpp-python== pin in {PIN_FILE}")


def detect_platform(names) -> str:
    """'linux' when the libraries are a Linux build (libllama.so), else 'windows' (#1198)."""
    return "linux" if "libllama.so" in set(names) else "windows"


def _full_version_copy(name: str) -> bool:
    """libX.so.0.25.3: `pip --target` turns the libX.so / libX.so.0 symlinks into full copies, so each library is there
    3 times. Keep libX.so (what llama-cpp-python loads) and libX.so.0 (the SONAME the others link against), drop this
    third copy (#1198: 40.6 -> 27 MB)."""
    parts = name.split(".so.", 1)
    return len(parts) == 2 and parts[1].count(".") >= 1


def build(lib_dir: Path, out_dir: Path, version: str) -> Path:
    """Zip every library of llama_cpp/lib (flat) + the manifest into out_dir/tlwu-vulkan-pack[-linux]-<version>.zip."""
    lib_dir = Path(lib_dir)
    plat = detect_platform(p.name for p in lib_dir.iterdir()) if lib_dir.is_dir() else "windows"
    spec = PLATFORMS[plat]
    dlls = sorted(p for p in lib_dir.glob(spec["glob"]) if p.is_file() and not _full_version_copy(p.name))
    have = {p.name for p in dlls}
    for need in spec["required"]:
        if need not in have:
            raise SystemExit(f"vulkan pack: {need} not found in {lib_dir} (build llama-cpp-python with GGML_VULKAN=on)")
    manifest = {"backend": "vulkan", "version": version, "llama_cpp_python": llama_pin(), "platform": plat,
                "files": [p.name for p in dlls]}
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    zpath = out_dir / spec["zip"].format(version=version)
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for p in dlls:
            z.write(p, p.name)
        z.writestr(MANIFEST_NAME, json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        # #915: the app version the pack was built for; mt_backend.vulkan_pack_dir() refuses a pack without it (#909)
        z.writestr(VERSION_FILE, version + "\n")
    return zpath


WHISPER_DIR = "whisper"  # = tinglongwu.asr_vulkan.WHISPER_DIR (#783)
WHISPER_LIBS = {"windows": "tlwu-whisper.dll", "linux": "libtlwu-whisper.so"}  # = asr_vulkan.WHISPER_LIB per platform


def add_whisper(zpath: Path, whisper_dir: Path) -> list[str]:
    """Rewrite the pack zip with whisper_dir's library (+ LICENSE / commit files) under whisper/ and the whisper.cpp
    commit in the manifest. Kept apart from the top-level llama.cpp libraries: both ship their own ggml."""
    zpath, whisper_dir = Path(zpath), Path(whisper_dir)
    with zipfile.ZipFile(zpath) as z:
        items = [(i, z.read(i.filename)) for i in z.infolist() if not i.filename.startswith(WHISPER_DIR + "/")]
    lib = WHISPER_LIBS[detect_platform(i.filename for i, _ in items)]
    if not (whisper_dir / lib).is_file():
        raise SystemExit(f"vulkan pack: {lib} not found in {whisper_dir} (scripts/build_whisper_vulkan.py --out)")
    extra = sorted(p for p in whisper_dir.iterdir() if p.is_file() and (p.name == lib or p.suffix == ".txt"))
    commit_file = whisper_dir / "whisper-cpp-commit.txt"
    added = []
    tmp = zpath.with_suffix(".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for info, data in items:
            if info.filename == MANIFEST_NAME:
                m = json.loads(data)
                m["files"] = [f for f in m.get("files", []) if not f.startswith(WHISPER_DIR + "/")]
                m["files"] += [f"{WHISPER_DIR}/{p.name}" for p in extra]
                if commit_file.is_file():
                    m["whisper_cpp"] = commit_file.read_text(encoding="utf-8").strip()
                data = (json.dumps(m, indent=2, sort_keys=True) + "\n").encode()
            z.writestr(info.filename, data)
        for p in extra:
            z.write(p, f"{WHISPER_DIR}/{p.name}")
            added.append(f"{WHISPER_DIR}/{p.name}")
    tmp.replace(zpath)
    return added


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
        vulkan_lib = PLATFORMS[detect_platform(p.name for p in d.iterdir())]["required"][1]
        return mt_backend.vulkan_pack_dir(d) == d.resolve() and (d / vulkan_lib).is_file()


def vulkan_devices(pack_dir: Path, load=ctypes.CDLL) -> list[str]:
    """Device descriptions the pack's ggml Vulkan backend enumerates (Mesa lavapipe on a GPU-less runner).
    Loading the library also proves its dependencies (libggml-base, libvulkan) resolve from the pack folder."""
    plat = detect_platform(p.name for p in Path(pack_dir).iterdir())
    lib = load(str(Path(pack_dir).resolve() / PLATFORMS[plat]["required"][1]))
    lib.ggml_backend_vk_get_device_count.restype = ctypes.c_int
    lib.ggml_backend_vk_get_device_description.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_size_t]
    out = []
    for i in range(lib.ggml_backend_vk_get_device_count()):
        buf = ctypes.create_string_buffer(256)
        lib.ggml_backend_vk_get_device_description(i, buf, len(buf))
        out.append(buf.value.decode("utf-8", "replace"))
    return out


def smoke(zpath: Path, load=ctypes.CDLL, out=print) -> int:
    """Unzip the pack, list its Vulkan devices; 0 when at least one is found."""
    with tempfile.TemporaryDirectory() as td, zipfile.ZipFile(zpath) as z:
        z.extractall(td)
        try:
            devs = vulkan_devices(Path(td), load)
        except (OSError, AttributeError) as e:
            out(f"vulkan smoke: FAIL {type(e).__name__}: {e}")
            return 1
    for i, d in enumerate(devs):
        out(f"vulkan device {i}: {d}")
    out(f"vulkan smoke: {len(devs)} device(s)" + ("" if devs else " - FAIL"))
    return 0 if devs else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--lib", help="llama_cpp/lib folder of a GGML_VULKAN=on build")
    ap.add_argument("--version", help="release version without the v (zip name)")
    ap.add_argument("--out", default="dist", help="output folder")
    ap.add_argument("--check", metavar="ZIP", help="list the zip layout and verify vulkan_pack_dir() accepts it")
    ap.add_argument("--smoke", metavar="ZIP", help="load the pack's Vulkan backend and list its devices")
    ap.add_argument("--add-whisper", metavar="ZIP", help="add the whisper.cpp Vulkan build (--whisper DIR) to ZIP")
    ap.add_argument("--whisper", metavar="DIR", help="with --add-whisper: build_whisper_vulkan.py --out folder")
    a = ap.parse_args(argv)
    if a.add_whisper:
        if not a.whisper:
            ap.error("--add-whisper needs --whisper DIR")
        for n in add_whisper(Path(a.add_whisper), Path(a.whisper)):
            print(f"added {n}")
        return 0
    if a.smoke:
        return smoke(Path(a.smoke))
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
