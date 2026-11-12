r"""Does the packaged tlwu.exe run on a clean Windows machine (no Python, no VC++ redistributable)? (#9)

Two checks, both without the owner's hands:

  python packaging\clean_machine_check.py deps  <dist\tlwu>   static: every DLL that an .exe/.dll/.pyd in the folder
                                                              imports must be shipped in the folder or be part of
                                                              Windows itself; runs anywhere (also on Linux CI).
  python packaging\clean_machine_check.py sandbox <dist\tlwu> [--out DIR] [--run] [--timeout 600]
                                                              live: writes a Windows Sandbox config (fresh Windows,
                                                              networking off, folder read-only) that runs
                                                              `tlwu.exe --smoke` at logon; --run starts it and waits.

Exit code 0 = OK, 1 = problem found (the missing DLLs or the failed smoke lines are printed).
"""
from __future__ import annotations

import argparse
import struct
import subprocess
import sys
import time
from pathlib import Path
from xml.sax.saxutils import escape

PE_SUFFIXES = (".exe", ".dll", ".pyd")
# DLLs every supported Windows 10/11 has. Not here on purpose: vcruntime140*.dll, msvcp140*.dll, vcomp140.dll,
# concrt140.dll (VC++ redistributable, absent on a clean machine) - those must be shipped in the folder.
SYSTEM_DLLS = frozenset("""
advapi32 avrt bcrypt bcryptprimitives cabinet cfgmgr32 combase comctl32 comdlg32 credui crypt32 d2d1 d3d11 d3d12 d3d9 dbgcore dbghelp
dnsapi dwmapi dwrite dxgi gdi32 gdiplus glu32 hid imagehlp imm32 iphlpapi kernel32 kernelbase ksuser mf mfplat
mfreadwrite mmdevapi mpr msimg32 msvcrt mswsock ncrypt netapi32 normaliz ntdll ole32 oleacc oleaut32 opengl32 pdh
powrprof propsys psapi rpcrt4 secur32 setupapi shcore shell32 shlwapi sspicli ucrtbase urlmon user32 userenv usp10
uxtheme version winhttp wininet winmm winspool wintrust wldap32 ws2_32 wsock32 wtsapi32 windowscodecs
""".split())
DRIVER_DLLS = frozenset({"nvcuda", "nvml", "nvapi64"})  # come with the NVIDIA driver; only GPU machines need them


def _stem(name: str) -> str:
    n = name.lower()
    return n.rsplit(".", 1)[0] if n.endswith((".dll", ".drv", ".exe", ".sys")) else n


def pe_imports(data: bytes) -> list[str]:
    """Names of the DLLs in the import table of a PE file (delay-loaded DLLs are not included). [] if not a PE."""
    try:
        if data[:2] != b"MZ":
            return []
        pe = struct.unpack_from("<I", data, 0x3C)[0]
        if data[pe:pe + 4] != b"PE\0\0":
            return []
        nsec, opt_size = struct.unpack_from("<H", data, pe + 6)[0], struct.unpack_from("<H", data, pe + 20)[0]
        opt = pe + 24
        magic = struct.unpack_from("<H", data, opt)[0]
        dirs = opt + {0x10B: 96, 0x20B: 112}[magic]
        imp_rva = struct.unpack_from("<I", data, dirs + 8)[0]  # data directory 1 = imports
        secs = [struct.unpack_from("<IIII", data, opt + opt_size + 40 * i + 8) for i in range(nsec)]

        def off(rva: int) -> int:
            for vsize, va, rsize, raw in secs:
                if va <= rva < va + max(vsize, rsize):
                    return rva - va + raw
            raise ValueError(f"rva {rva:#x} outside sections")

        names, d = [], off(imp_rva) if imp_rva else None
        while d is not None:
            desc = struct.unpack_from("<5I", data, d)
            if not any(desc):
                break
            s = off(desc[3])
            names.append(data[s:data.index(b"\0", s)].decode("ascii", "replace"))
            d += 20
        return names
    except (struct.error, KeyError, ValueError):
        return []


def missing_dlls(app: Path) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """({dll: [files that import it]} not found anywhere, {driver dll: [importers]}) for a built app folder."""
    files = [f for f in app.rglob("*") if f.is_file()]
    shipped = {_stem(f.name) for f in files if f.suffix.lower() in PE_SUFFIXES}
    missing: dict[str, list[str]] = {}
    driver: dict[str, list[str]] = {}
    for f in files:
        if f.suffix.lower() not in PE_SUFFIXES:
            continue
        for dll in pe_imports(f.read_bytes()):
            s = _stem(dll)
            if s in shipped or s in SYSTEM_DLLS or s.startswith(("api-ms-win-", "ext-ms-")):
                continue
            (driver if s in DRIVER_DLLS else missing).setdefault(dll.lower(), []).append(str(f.relative_to(app)))
    return missing, driver


def cmd_deps(app: Path) -> int:
    if not (app / "tlwu.exe").is_file():
        print(f"tlwu.exe not found in {app}")
        return 1
    missing, driver = missing_dlls(app)
    for dll, users in sorted(driver.items()):
        print(f"driver (GPU machines only): {dll} <- {', '.join(sorted(users)[:3])}")
    for dll, users in sorted(missing.items()):
        print(f"MISSING on a clean Windows: {dll} <- {', '.join(sorted(users)[:3])}")
    print(f"deps: {'FAIL' if missing else 'OK'} ({len(missing)} missing DLL(s))")
    return 1 if missing else 0


RUN_PS1 = r"""$o = 'C:\tlwu-check'
$py = if (Get-Command python -ErrorAction SilentlyContinue) { 'python found' } else { 'no python' }
Set-Content "$o\machine.txt" $py
$p = Start-Process -FilePath 'C:\tlwu\tlwu.exe' -ArgumentList '--smoke' -Wait -PassThru -NoNewWindow `
  -RedirectStandardOutput "$o\smoke.txt" -RedirectStandardError "$o\smoke-err.txt"
Set-Content "$o\exitcode.txt" $p.ExitCode
shutdown /s /t 5
"""


def write_sandbox(app: Path, out: Path) -> Path:
    """Write tlwu-clean.wsb + run.ps1 into out; return the .wsb path. Old results in out are removed."""
    out.mkdir(parents=True, exist_ok=True)
    for name in ("exitcode.txt", "smoke.txt", "smoke-err.txt", "machine.txt"):
        (out / name).unlink(missing_ok=True)
    (out / "run.ps1").write_text(RUN_PS1, "utf-8", newline="\r\n")
    wsb = out / "tlwu-clean.wsb"
    wsb.write_text(f"""<Configuration>
  <VGpu>Disable</VGpu>
  <Networking>Disable</Networking>
  <MappedFolders>
    <MappedFolder><HostFolder>{escape(str(app.resolve()))}</HostFolder><SandboxFolder>C:\\tlwu</SandboxFolder><ReadOnly>true</ReadOnly></MappedFolder>
    <MappedFolder><HostFolder>{escape(str(out.resolve()))}</HostFolder><SandboxFolder>C:\\tlwu-check</SandboxFolder><ReadOnly>false</ReadOnly></MappedFolder>
  </MappedFolders>
  <LogonCommand><Command>powershell -NoProfile -ExecutionPolicy Bypass -File C:\\tlwu-check\\run.ps1</Command></LogonCommand>
</Configuration>
""", "utf-8")
    return wsb


def read_result(out: Path) -> int | None:
    """Exit code of `tlwu.exe --smoke` in the sandbox, None while it has not finished."""
    f = out / "exitcode.txt"
    if not f.exists():
        return None
    text = f.read_text("utf-8-sig", errors="replace").strip()
    return int(text) if text.lstrip("-").isdigit() else 1


def cmd_sandbox(app: Path, out: Path, run: bool, timeout: float, poll: float = 5.0) -> int:
    wsb = write_sandbox(app, out)
    print(f"Windows Sandbox config: {wsb}")
    if not run:
        print("Open it (double-click, needs the Windows Sandbox feature), or rerun with --run to start it and wait.")
        return 0
    subprocess.Popen(["WindowsSandbox.exe", str(wsb)])
    deadline = time.monotonic() + timeout
    while (code := read_result(out)) is None:
        if time.monotonic() > deadline:
            print(f"sandbox: TIMEOUT after {timeout:.0f}s (no {out / 'exitcode.txt'})")
            return 1
        time.sleep(poll)
    for name in ("machine.txt", "smoke.txt", "smoke-err.txt"):
        if (out / name).exists():
            print((out / name).read_text("utf-8", errors="replace").rstrip())
    print(f"sandbox: {'OK' if code == 0 else 'FAIL'} (tlwu.exe --smoke exit {code})")
    return 0 if code == 0 else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("deps").add_argument("app", type=Path)
    sb = sub.add_parser("sandbox")
    sb.add_argument("app", type=Path)
    sb.add_argument("--out", type=Path, default=Path("sandbox-check"))
    sb.add_argument("--run", action="store_true")
    sb.add_argument("--timeout", type=float, default=600)
    a = ap.parse_args(argv)
    if a.cmd == "deps":
        return cmd_deps(a.app)
    return cmd_sandbox(a.app, a.out, a.run, a.timeout)


if __name__ == "__main__":
    sys.exit(main())
