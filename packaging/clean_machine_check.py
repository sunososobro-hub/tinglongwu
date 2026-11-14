r"""Does the packaged tlwu.exe run on a clean Windows machine (no Python, no VC++ redistributable)? (#9)

Two checks, both without the owner's hands:

  python packaging\clean_machine_check.py deps  <dist\tlwu>   static: every DLL that an .exe/.dll/.pyd in the folder
                                                              imports must be shipped in the folder or be part of
                                                              Windows itself; runs anywhere (also on Linux CI).
  python packaging\clean_machine_check.py sandbox <dist\tlwu> [--out DIR] [--run] [--timeout 600]
                                                              live: writes a Windows Sandbox config (fresh Windows,
                                                              networking off, folder read-only) that runs
                                                              `tlwu.exe --smoke` at logon; --run starts it and waits.

  python packaging\clean_machine_check.py setup-time <setup.exe> [--out DIR] [--run] [--timeout 1800]
                                                              live (#769): Windows Sandbox (networking ON) runs the wizard
                                                              silently, writes setup_time.json; the judge gates setup time
                                                              minus download time, pip lines and readiness.

Exit code 0 = OK, 1 = problem found (the missing DLLs or the failed smoke lines are printed).
"""
from __future__ import annotations

import argparse
import json
import re
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


SETUP_READY_BUDGET_S = 60  # #752: wizard start -> ready, excluding the model download
BASELINE_MBPS = 100  # the connection the budget assumes (reported, the download share is not gated)
INSTALL_LOG = r"$env:LOCALAPPDATA\Programs\tinglongWu\install.log"
READY_RE = re.compile(r"READY default-listen seconds=(?P<s>[0-9.]+) download_mb=(?P<mb>\d+)")
PIP_RE = re.compile(r"pip(?:3)?(?:\.exe)?\s+install|-m\s+pip\s+install", re.I)

SETUP_PS1 = r"""$o = 'C:\tlwu-check'
$log = "%LOG%"
$t0 = Get-Date
$p = Start-Process -FilePath 'C:\tlwu-setup\%SETUP%' -ArgumentList '/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART' -Wait -PassThru
$secs = ((Get-Date) - $t0).TotalSeconds
Set-Content "$o\setup_seconds.txt" $secs
Set-Content "$o\setup_exit.txt" $p.ExitCode
if (Test-Path $log) { Copy-Item $log "$o\install.log" -Force }
shutdown /s /t 5
"""


def parse_install_log(text: str) -> dict:
    """{"ready", "download_seconds", "download_mb", "pip_lines"} from install.log text."""
    ready, dl_s, dl_mb = False, 0.0, 0
    for m in READY_RE.finditer(text):
        ready, dl_s, dl_mb = True, float(m["s"]), int(m["mb"])
    return {"ready": ready, "download_seconds": dl_s, "download_mb": dl_mb,
            "pip_lines": sum(1 for ln in text.splitlines() if PIP_RE.search(ln))}


def build_result(setup_seconds: float, log_text: str) -> dict:
    return {"setup_seconds": round(setup_seconds, 1), **parse_install_log(log_text)}


def judge(result: dict) -> list[str]:
    """One line per check; a line starting with FAIL means exit 1."""
    own = float(result["setup_seconds"]) - float(result["download_seconds"])
    mb = result.get("download_mb", 0)
    lines = [
        f"{'OK' if result['ready'] else 'FAIL'}: ready (READY default-listen line {'found' if result['ready'] else 'missing'})",
        f"{'OK' if own <= SETUP_READY_BUDGET_S else 'FAIL'}: setup minus download {own:.1f}s (budget {SETUP_READY_BUDGET_S}s)",
        f"{'OK' if result['pip_lines'] == 0 else 'FAIL'}: pip install lines in install.log: {result['pip_lines']}",
        f"INFO: download {result['download_seconds']:.1f}s, {mb} MB (not gated; baseline {BASELINE_MBPS} Mbps)",
    ]
    return lines


def write_setup_sandbox(setup: Path, out: Path) -> Path:
    """Write setup-time.wsb + run_setup.ps1 into out (networking ON: the wizard downloads the model)."""
    out.mkdir(parents=True, exist_ok=True)
    for name in ("setup_time.json", "setup_seconds.txt", "setup_exit.txt", "install.log"):
        (out / name).unlink(missing_ok=True)
    ps1 = SETUP_PS1.replace("%SETUP%", setup.name).replace("%LOG%", INSTALL_LOG)
    (out / "run_setup.ps1").write_text(ps1, "utf-8", newline="\r\n")
    wsb = out / "setup-time.wsb"
    wsb.write_text(f"""<Configuration>
  <VGpu>Disable</VGpu>
  <Networking>Enable</Networking>
  <MappedFolders>
    <MappedFolder><HostFolder>{escape(str(setup.resolve().parent))}</HostFolder><SandboxFolder>C:\\tlwu-setup</SandboxFolder><ReadOnly>true</ReadOnly></MappedFolder>
    <MappedFolder><HostFolder>{escape(str(out.resolve()))}</HostFolder><SandboxFolder>C:\\tlwu-check</SandboxFolder><ReadOnly>false</ReadOnly></MappedFolder>
  </MappedFolders>
  <LogonCommand><Command>powershell -NoProfile -ExecutionPolicy Bypass -File C:\\tlwu-check\\run_setup.ps1</Command></LogonCommand>
</Configuration>
""", "utf-8")
    return wsb


def collect_setup_result(out: Path) -> dict | None:
    """Build and write setup_time.json from the sandbox outputs; None while the sandbox has not finished."""
    f = out / "setup_seconds.txt"
    if not f.exists():
        return None
    secs = float(f.read_text("utf-8-sig", errors="replace").strip() or 0)
    log = out / "install.log"
    res = build_result(secs, log.read_text("utf-8-sig", errors="replace") if log.exists() else "")
    (out / "setup_time.json").write_text(json.dumps(res, indent=2), "utf-8")
    return res


def cmd_setup_time(setup: Path, out: Path, run: bool, timeout: float, poll: float = 5.0) -> int:
    wsb = write_setup_sandbox(setup, out)
    print(f"Windows Sandbox config: {wsb}")
    if not run:
        print("Open it (needs the Windows Sandbox feature), or rerun with --run to start it and wait.")
        return 0
    subprocess.Popen(["WindowsSandbox.exe", str(wsb)])
    deadline = time.monotonic() + timeout
    while (res := collect_setup_result(out)) is None:
        if time.monotonic() > deadline:
            print(f"setup-time: TIMEOUT after {timeout:.0f}s (no {out / 'setup_seconds.txt'})")
            return 1
        time.sleep(poll)
    lines = judge(res)
    print("\n".join(lines))
    bad = any(ln.startswith("FAIL") for ln in lines)
    print(f"setup-time: {'FAIL' if bad else 'OK'}")
    return 1 if bad else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("deps").add_argument("app", type=Path)
    sb = sub.add_parser("sandbox")
    sb.add_argument("app", type=Path)
    sb.add_argument("--out", type=Path, default=Path("sandbox-check"))
    sb.add_argument("--run", action="store_true")
    sb.add_argument("--timeout", type=float, default=600)
    st = sub.add_parser("setup-time")
    st.add_argument("setup", type=Path)
    st.add_argument("--out", type=Path, default=Path("setup-time-check"))
    st.add_argument("--run", action="store_true")
    st.add_argument("--timeout", type=float, default=1800)
    a = ap.parse_args(argv)
    if a.cmd == "setup-time":
        return cmd_setup_time(a.setup, a.out, a.run, a.timeout)
    if a.cmd == "deps":
        return cmd_deps(a.app)
    return cmd_sandbox(a.app, a.out, a.run, a.timeout)


if __name__ == "__main__":
    sys.exit(main())
