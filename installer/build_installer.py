r"""Build the Windows installer wizard (tinglongwu-setup-<version>.exe) with Inno Setup 6 (#125, part 1).

    python installer\build_installer.py            stage files + icon, then compile with ISCC.exe
    python installer\build_installer.py --dry-run  stage files + icon and print the ISCC command only
    python installer\build_installer.py --core-dir dist\tlwu   wizard for the prebuilt core (tlwu.exe, #767)

Stdlib only (runs from any Python 3.10+, also on Linux for the tests). The wizard ships the git-tracked app files
(no tests, no .venv, no models) with the app icon tinglongwu/gui/assets/tinglongwu.ico, then runs install.ps1 through installer\run_install.ps1 and shows its steps.
With --core-dir DIR the wizard instead ships the prebuilt Nuitka core folder DIR (tlwu.exe at the top) + LICENSE and
its shortcuts start {app}\tlwu.exe; no install.ps1, no Python, no pip (the .iss `CoreLayout` define).
ISCC.exe is found via %ISCC%, PATH, or the default Inno Setup 6 folders. Inno Setup is free (see LICENSE-InnoSetup.txt).
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import struct
import subprocess
import sys
import zlib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ISS = REPO / "installer" / "tinglongwu.iss"
OUT = REPO / "dist" / "installer"
# what the installed app needs at run time and for install.ps1 / update.bat / the menus
STAGE_PREFIXES = ("tinglongwu/", "tinglongwu_setup/", "tlwu_app/", "scripts/", "installer/run_install.ps1",
                  "install.ps1", "install.bat", "update.bat", "pyproject.toml", "requirements", "LICENSE",
                  "README", "PRIVACY", "SECURITY.md")
ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)
# the one app icon (#270, #127): the wizard, its shortcuts, the uninstall entry, install.ps1 shortcuts and tlwu.exe
APP_ICON = REPO / "tinglongwu" / "gui" / "assets" / "tinglongwu.ico"


def app_version(repo: Path = REPO) -> str:
    # tinglongwu.__version__ is the single source of truth (#462); read as text, no import
    m = re.search(r'^__version__\s*=\s*"([^"]+)"', (repo / "tinglongwu" / "__init__.py").read_text("utf-8"), re.M)
    return m.group(1) if m else "0.0.0"


def staged_files(repo: Path = REPO) -> list[str]:
    """Git-tracked files the installer ships (posix paths relative to the repo)."""
    out = subprocess.run(["git", "ls-files", "-z"], cwd=repo, capture_output=True, check=True).stdout
    names = [n for n in out.decode("utf-8").split("\0") if n]
    return sorted(n for n in names if n.startswith(STAGE_PREFIXES) and (repo / n).is_file())


def stage(dst: Path, repo: Path = REPO) -> list[str]:
    if dst.exists():
        shutil.rmtree(dst)
    files = staged_files(repo)
    for n in files:
        (dst / n).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(repo / n, dst / n)
    return files


# #767: the prebuilt core wizard. The exe the shortcuts start, and files of the source layout that must never ship
CORE_EXE = "tlwu.exe"
_NOT_IN_CORE = ("install.ps1", "install.bat", "requirements", ".venv/", "installer/run_install.ps1")


def stage_core(dst: Path, core_dir: Path, repo: Path = REPO) -> list[str]:
    """Copy the prebuilt core folder (tlwu.exe at the top) + the repo LICENSE (wizard license page) into dst."""
    core_dir = Path(core_dir)
    if not (core_dir / CORE_EXE).is_file():
        raise SystemExit(f"--core-dir {core_dir}: {CORE_EXE} not found at the top of the folder")
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(core_dir, dst)
    shutil.copy2(repo / "LICENSE", dst / "LICENSE")
    files = sorted(p.relative_to(dst).as_posix() for p in dst.rglob("*") if p.is_file())
    bad = [f for f in files if f.startswith(_NOT_IN_CORE)]
    if bad:
        raise SystemExit(f"--core-dir {core_dir}: source-install files do not belong in the core wizard: {bad}")
    return files


# ------------------------------------------------------------------ app icon (drawn in code, no binary in git)
def _coverage(size: int, x: int, y: int, inside) -> float:
    n = 4  # 4x4 supersampling for anti-aliased edges
    return sum(inside((x + (i + .5) / n) / size, (y + (j + .5) / n) / size) for i in range(n) for j in range(n)) / n / n


def _rounded(u: float, v: float, x0: float, y0: float, x1: float, y1: float, r: float) -> bool:
    cx, cy = min(max(u, x0 + r), x1 - r), min(max(v, y0 + r), y1 - r)
    return x0 <= u <= x1 and y0 <= v <= y1 and (u - cx) ** 2 + (v - cy) ** 2 <= r * r


def _bubble(u: float, v: float) -> bool:  # speech bubble with a tail at the bottom left
    tail = 0.24 <= u <= 0.42 and 0.66 <= v <= 0.82 and (u - 0.24) >= (v - 0.66) * 1.1
    return _rounded(u, v, 0.16, 0.20, 0.84, 0.70, 0.14) or tail


def _bars(u: float, v: float) -> bool:  # sound-wave bars inside the bubble
    for cx, h in ((0.32, 0.10), (0.42, 0.18), (0.52, 0.24), (0.62, 0.16), (0.70, 0.08)):
        if _rounded(u, v, cx - 0.03, 0.45 - h, cx + 0.03, 0.45 + h, 0.03):
            return True
    return False


def icon_rgba(size: int) -> bytes:
    bg = (15, 118, 110)  # teal 700
    px = bytearray()
    for y in range(size):
        for x in range(size):
            a_bg = _coverage(size, x, y, lambda u, v: _rounded(u, v, 0.02, 0.02, 0.98, 0.98, 0.20))
            a_w = _coverage(size, x, y, lambda u, v: _bubble(u, v) and not _bars(u, v))
            rgb = [round(c * (1 - a_w) + 255 * a_w) for c in bg]
            px += bytes(rgb) + bytes([round(255 * max(a_bg, a_w))])
    return bytes(px)


def _png(size: int, rgba: bytes) -> bytes:
    def chunk(t: bytes, d: bytes) -> bytes:
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    raw = b"".join(b"\0" + rgba[y * size * 4:(y + 1) * size * 4] for y in range(size))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def make_icon(path: Path, sizes: tuple[int, ...] | None = None) -> Path:
    """Multi-size .ico (PNG-compressed entries, supported since Windows Vista)."""
    sizes = sizes or ICON_SIZES
    pngs = [_png(s, icon_rgba(s)) for s in sizes]
    head = struct.pack("<HHH", 0, 1, len(sizes))
    off = 6 + 16 * len(sizes)
    dirs = b""
    for s, p in zip(sizes, pngs):
        dirs += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(p), off)
        off += len(p)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(head + dirs + b"".join(pngs))
    return path


def wizard_icon(path: Path, src: Path | None = None) -> Path:
    """The installer icon: a copy of the committed app icon, so wizard, shortcuts and tlwu.exe look the same.
    Drawn with make_icon() only if the asset is missing."""
    src = src or APP_ICON
    if not src.is_file():
        return make_icon(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, path)
    return path


# ------------------------------------------------------------------ Inno Setup compiler
def find_iscc() -> str | None:
    cands = [os.environ.get("ISCC", ""), shutil.which("ISCC") or "", shutil.which("iscc") or ""]
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA")):
        if base:
            cands += [str(Path(base) / "Inno Setup 6" / "ISCC.exe"), str(Path(base) / "Programs" / "Inno Setup 6" / "ISCC.exe")]
    return next((c for c in cands if c and Path(c).is_file()), None)


# Disk the real install needs beyond the staged files (#701): Python 3.12 ~0.1 GB + ~3 GB pip packages
# + 2-3 GB default models (figures from the issue / progress page text). Inno shows it on the destination page
# and refuses Next when the drive has less.
EXTRA_DISK_BYTES = 6 * 1024**3
# #767: the prebuilt core needs no Python or pip packages, only the default listen models it fetches later (#766:
# whisper-small ~0.5 GB + Hy-MT2 1.8B GGUF ~1.1 GB) with headroom for one more pack.
EXTRA_DISK_BYTES_CORE = 2 * 1024**3


def iscc_cmd(iscc: str, stage_dir: Path, icon: Path, out_dir: Path, version: str, core: bool = False) -> list[str]:
    extra = EXTRA_DISK_BYTES_CORE if core else EXTRA_DISK_BYTES
    return [iscc, f"/DStageDir={stage_dir}", f"/DIconFile={icon}", f"/DOutDir={out_dir}", f"/DAppVersion={version}",
            f"/DExtraDiskBytes={extra}", *(["/DCoreLayout=1"] if core else []), str(ISS)]


def _given(cmd: list[str]) -> set[str]:
    return {a[2:].split("=", 1)[0] for a in cmd if a.startswith("/D")}


def preprocess(iss_text: str, defines: set[str]) -> str:
    """The script ISCC compiles for these /D defines: keeps the #ifdef / #ifndef / #else / #endif branches that apply
    (Inno Setup Preprocessor semantics, nesting allowed). Lets the tests check each layout on Linux without ISCC."""
    out, stack = [], []  # stack of (this branch active, parent active)
    for line in iss_text.splitlines():
        m = re.match(r"\s*#(ifdef|ifndef|else|endif)\b\s*(\w*)", line)
        if m:
            kw, name = m.groups()
            parent = all(a for a, _ in stack[-1:]) if stack else True
            if kw in ("ifdef", "ifndef"):
                stack.append(((name in defines) == (kw == "ifdef") and parent, parent))
            elif kw == "else":
                act, par = stack.pop()
                stack.append((par and not act, par))
            else:
                stack.pop()
            continue
        if all(a for a, _ in stack):
            out.append(line)
    if stack:
        raise ValueError("unbalanced #ifdef/#endif in the .iss")
    return "\n".join(out) + "\n"


def effective_iss(cmd: list[str], iss_text: str | None = None) -> str:
    return preprocess(ISS.read_text("utf-8-sig") if iss_text is None else iss_text, _given(cmd))


def missing_defines(iss_text: str, cmd: list[str]) -> list[str]:
    """{#Name} preprocessor defines the .iss uses but the ISCC command does not pass (= compile error)."""
    iss_text = preprocess(iss_text, _given(cmd))
    used = set(re.findall(r"\{#(\w+)\}", iss_text)) - set(re.findall(r"^#define\s+(\w+)", iss_text, re.M))
    return sorted(used - _given(cmd))


# #772: the release job reports the core wizard's size; over this it warns (never fails: a release is not blocked
# by size alone). Same 300 MB budget as the core zip (scripts/release_check.py MAX_CORE_MB) since it wraps that core.
WIZARD_MAX_MB = 300
BYTES_PER_MB = 1 << 20


def wizard_size_line(exe: Path, max_mb: float = WIZARD_MAX_MB) -> tuple[bool, str]:
    """(within budget, one line) for the built wizard `exe`."""
    size_mb = Path(exe).stat().st_size / BYTES_PER_MB
    ok = size_mb <= max_mb
    return ok, f"setup wizard {Path(exe).name}: {size_mb:.1f} MB ({'within' if ok else 'OVER'} the {max_mb:g} MB budget)"


def size_report(exe: Path, max_mb: float = WIZARD_MAX_MB) -> int:
    """Print the size line (+ a GitHub ::warning:: when over), append it to $GITHUB_STEP_SUMMARY if set. Exit 0."""
    exe = Path(exe)
    if not exe.is_file():
        print(f"size report: {exe} not found", file=sys.stderr)
        return 2
    ok, line = wizard_size_line(exe, max_mb)
    print(line)
    if not ok:
        print(f"::warning::{line}")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(f"- {line}\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--size-report", metavar="EXE", help="print the built wizard's size vs WIZARD_MAX_MB and exit (#772)")
    p.add_argument("--dry-run", action="store_true", help="stage + icon, print the ISCC command, do not compile")
    p.add_argument("--out", default=str(OUT))
    p.add_argument("--core-dir", help="prebuilt core folder (tlwu.exe at the top, e.g. dist/tlwu): the wizard installs "
                                      "it and starts tlwu.exe; no install.ps1 / Python / pip (#767)")
    a = p.parse_args(argv)
    if a.size_report:
        return size_report(Path(a.size_report))
    out = Path(a.out)
    files = stage_core(out / "stage", Path(a.core_dir)) if a.core_dir else stage(out / "stage")
    icon = wizard_icon(out / "tinglongwu.ico")
    print(f"staged {len(files)} files -> {out / 'stage'}; icon -> {icon}")
    cmd = iscc_cmd(find_iscc() or "ISCC.exe", out / "stage", icon, out, app_version(), core=bool(a.core_dir))
    miss = missing_defines(ISS.read_text("utf-8-sig"), cmd)
    if miss:
        print(f"tinglongwu.iss uses undefined {miss}", file=sys.stderr)
        return 1
    print(" ".join(f'"{c}"' if " " in c else c for c in cmd))
    if a.dry_run:
        return 0
    if not find_iscc():
        print("ISCC.exe not found: install Inno Setup 6 (https://jrsoftware.org/isdl.php) or set ISCC=<path>",
              file=sys.stderr)
        return 2
    return subprocess.call(cmd)


if __name__ == "__main__":
    sys.exit(main())
