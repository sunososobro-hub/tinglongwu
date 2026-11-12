r"""Can every native module in a packaged dist folder find the DLLs it imports? (#398)

    python scripts\check_dist_dlls.py <dist\tlwu> [--system-dir DIR ...]

For every .dll/.pyd/.exe in the folder, each DLL in its import table is looked up the way the app loads it:
the importer's own folder, the app folder, the folders the app adds at run time (cuda_libs/, llama_cpp/lib/,
llama_cpp/lib_cpu/, the delvewheel *.libs/ folders), then the system folder (System32 on Windows; on other OSes
a fixed list of DLLs every Windows 10/11 has). api-ms-win-*/ext-ms-* API sets always resolve. NVIDIA driver DLLs
(nvcuda.dll) are listed but do not fail the check: a PC without an NVIDIA GPU runs the CPU llama build instead.

Prints one MISSING line per unresolved DLL and exits 1 if there is any (0 = every dependent resolves).
The #398 build shipped a CUDA llama.dll without cudart64_12.dll / cublas64_12.dll: "Could not find module".
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "packaging"))
from clean_machine_check import DRIVER_DLLS, PE_SUFFIXES, SYSTEM_DLLS, pe_imports  # noqa: E402

RUNTIME_DIRS = ("cuda_libs", "llama_cpp/lib", "llama_cpp/lib_cpu")  # os.add_dll_directory'd by the app


def _stem(name: str) -> str:
    return name.lower().rsplit(".", 1)[0]


def search_dirs(app: Path, importer: Path) -> list[Path]:
    """Folders searched for a DLL imported by `importer`, in loader order (without the system folder)."""
    dirs = [importer.parent, app] + [app / d for d in RUNTIME_DIRS] + sorted(app.glob("*.libs"))
    out: list[Path] = []
    for d in dirs:
        if d not in out:
            out.append(d)
    return out


def resolve(dll: str, dirs: list[Path], listing: dict[Path, set[str]], system: set[str]) -> str | None:
    """Where `dll` is found: a folder path, "system", "apiset", "driver", or None (missing).

    `listing` maps a folder to the lower-case file names in it, `system` holds the lower-case names (with or
    without .dll) that the system folder provides."""
    name = dll.lower()
    if name.startswith(("api-ms-win-", "ext-ms-")):
        return "apiset"
    for d in dirs:
        if name in listing.get(d, ()):
            return str(d)
    if name in system or _stem(name) in system:
        return "system"
    if _stem(name) in DRIVER_DLLS:
        return "driver"
    return None


def system_names(system_dirs: list[Path]) -> set[str]:
    names = {n.lower() for d in system_dirs if d.is_dir() for n in os.listdir(d)}
    return names or set(SYSTEM_DLLS)


def default_system_dirs() -> list[Path]:
    root = os.environ.get("SystemRoot")
    return [Path(root) / "System32"] if sys.platform == "win32" and root else []


def unresolved(app: Path, system: set[str], read=lambda p: p.read_bytes()):
    """({missing dll: [importers]}, {driver dll: [importers]}) for the dist folder `app`."""
    files = [f for f in app.rglob("*") if f.is_file()]
    listing: dict[Path, set[str]] = {}
    for f in files:
        listing.setdefault(f.parent, set()).add(f.name.lower())
    missing: dict[str, list[str]] = {}
    driver: dict[str, list[str]] = {}
    for f in files:
        if f.suffix.lower() not in PE_SUFFIXES:
            continue
        dirs = search_dirs(app, f)
        for dll in pe_imports(read(f)):
            where = resolve(dll, dirs, listing, system)
            if where is None or where == "driver":
                (missing if where is None else driver).setdefault(dll.lower(), []).append(
                    f.relative_to(app).as_posix())
    return missing, driver


def check(app: Path, system_dirs: list[Path] | None = None, out=None) -> int:
    out = out or sys.stdout
    if not app.is_dir():
        print(f"not a folder: {app}", file=out)
        return 1
    missing, driver = unresolved(app, system_names(default_system_dirs() if system_dirs is None else system_dirs))
    for dll, users in sorted(driver.items()):
        print(f"driver (NVIDIA GPU only): {dll} <- {', '.join(sorted(users)[:3])}", file=out)
    for dll, users in sorted(missing.items()):
        print(f"MISSING: {dll} <- {', '.join(sorted(users)[:4])}", file=out)
    print(f"check_dist_dlls: {'FAIL' if missing else 'OK'} ({len(missing)} unresolved DLL(s))", file=out)
    return 1 if missing else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("app", type=Path, help="dist folder, e.g. dist\\tlwu")
    ap.add_argument("--system-dir", type=Path, action="append",
                    help="system DLL folder (default: %%SystemRoot%%\\System32 on Windows, a built-in list elsewhere)")
    a = ap.parse_args(argv)
    return check(a.app, a.system_dir)


if __name__ == "__main__":
    sys.exit(main())
