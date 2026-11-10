"""'Report a problem': build a pre-filled GitHub new-issue URL and open it in the system browser.

Nothing is uploaded by the app: the user reviews the issue text on GitHub and submits it themselves.
Paths and the user name are redacted from the text before it goes into the URL.
"""
from __future__ import annotations

import getpass
import os
import platform
import re
import sys
from pathlib import Path
from urllib.parse import quote

from . import userdirs

ISSUE_URL = "https://github.com/sunososobro-hub/tinglongwu/issues/new"
MAX_URL = 6000
LOG_LINES = 20


def _version() -> str:
    try:
        from . import __version__

        return str(__version__)
    except ImportError:
        return "unknown"


def redact(text: str) -> str:
    """Hide the home directory, the user name and Windows/POSIX user paths."""
    home = os.path.expanduser("~")
    if home and home not in ("~", "/"):
        text = text.replace(home, "~")
    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001
        user = ""
    if user and len(user) >= 2:
        text = re.sub(re.escape(user), "<user>", text, flags=re.IGNORECASE)
    text = re.sub(r"(?i)([A-Z]:\\Users\\)[^\\\s]+", r"\1<user>", text)
    text = re.sub(r"(/(?:home|Users)/)[^/\s]+", r"\1<user>", text)
    return text


PF_AVX2, PF_AVX512F = 40, 41  # winnt.h PF_AVX2_INSTRUCTIONS_AVAILABLE / PF_AVX512F_INSTRUCTIONS_AVAILABLE


def _win_cpu(present=None, name=None) -> tuple[str, list[str] | None]:
    """Windows: CPU name from the registry, AVX2/AVX-512 from kernel32.IsProcessorFeaturePresent."""
    if name is None:
        def name():
            import winreg

            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as k:
                return winreg.QueryValueEx(k, "ProcessorNameString")[0]
    if present is None:
        import ctypes

        present = ctypes.windll.kernel32.IsProcessorFeaturePresent
    try:
        model = str(name()).strip()
    except Exception:  # noqa: BLE001
        model = ""
    try:
        feats = [n for n, f in (("AVX2", PF_AVX2), ("AVX-512", PF_AVX512F)) if present(f)]
    except Exception:  # noqa: BLE001
        feats = None
    return model, feats


def _cpu_features(system: str | None = None, cpuinfo="/proc/cpuinfo", win=_win_cpu) -> str:
    """CPU model + AVX2/AVX-512: Windows asks the OS, Linux reads /proc/cpuinfo; 'unknown' when neither works
    (never 'absent' unless the OS really answered)."""
    model, feats = platform.processor() or platform.machine(), None
    if (system or sys.platform) == "win32":
        m, feats = win()
        model = m or model
    else:
        try:
            for line in Path(cpuinfo).read_text("utf-8", errors="replace").splitlines():
                if line.startswith("model name") and ":" in line:
                    model = line.split(":", 1)[1].strip()
                elif line.startswith("flags") and ":" in line:
                    fl = set(line.split(":", 1)[1].split())
                    feats = [n for n, f in (("AVX2", "avx2"), ("AVX-512", "avx512f")) if f in fl]
                    break
        except OSError:
            pass
    tag = "AVX2/AVX-512 unknown" if feats is None else ", ".join(feats) or "no AVX2, no AVX-512"
    return f"{model} [{tag}]"


def _pkg_versions(names=("ctranslate2", "faster-whisper", "llama-cpp-python")) -> str:
    """Versions from <name>-<ver>.dist-info folder names on sys.path (never imports the packages: a broken
    wheel such as the AVX-512 llama-cpp one can crash the interpreter on import)."""
    found = {}
    for entry in sys.path:
        try:
            for d in Path(entry or ".").glob("*.dist-info"):
                stem = d.name[: -len(".dist-info")]
                name, _, ver = stem.rpartition("-")
                found.setdefault(name.lower().replace("_", "-"), ver)
        except OSError:
            continue
    return ", ".join(f"{n} {found[n]}" if n in found else f"{n} not installed" for n in names)


def _gpu_summary() -> dict:
    from . import hwprobe

    info = {}
    name, vram = hwprobe.gpu_info()
    info["GPU"] = f"{name} ({vram} MiB)" if name else "none (no nvidia-smi)"
    from .device import driver_version

    info["NVIDIA driver"] = driver_version() or "unknown"
    try:
        import ctranslate2

        info["ctranslate2 CUDA devices"] = str(ctranslate2.get_cuda_device_count())
    except Exception as e:  # noqa: BLE001
        info["ctranslate2 CUDA devices"] = f"unavailable ({type(e).__name__})"
    return info


def env_info() -> dict:
    """Environment facts that make a report actionable. Local only; no subtitle/translation text."""
    from . import hwprobe

    info = {"CPU": _cpu_features(), "Cores": str(os.cpu_count()), "RAM MiB": str(hwprobe.ram_mib())}
    info.update(_gpu_summary())
    info["Packages"] = _pkg_versions()
    return info


INSTALL_LOG = "install.log"  # written by the installer self-test (`tlwu --selftest`, #90)
SELFTEST_LINES = 12


def last_log_lines(n: int = LOG_LINES) -> list[str]:
    """Last n lines of the newest run log (*.log except install.log) in the log directory; [] when none."""
    try:
        logs = sorted((p for p in userdirs.log_dir().glob("*.log") if p.name != INSTALL_LOG),
                      key=lambda p: p.stat().st_mtime)
        if not logs:
            return []
        return logs[-1].read_text("utf-8", errors="replace").splitlines()[-n:]
    except OSError:
        return []


def selftest_lines(n: int = SELFTEST_LINES) -> list[str]:
    """The newest self-test block of install.log (from its last '--- ' header, at most n lines); [] when none."""
    try:
        lines = (userdirs.log_dir() / INSTALL_LOG).read_text("utf-8", errors="replace").splitlines()
    except OSError:
        return []
    starts = [i for i, ln in enumerate(lines) if ln.startswith("--- ")]
    return [ln for ln in lines[starts[-1] if starts else 0:] if ln.strip()][:n]


def build_body(info: dict | None = None, log_lines: list[str] | None = None,
               selftest: list[str] | None = None) -> str:
    info = dict(info or {})
    rows = [("App version", _version()),
            ("OS", f"{platform.system()} {platform.release()}"),
            ("Python", platform.python_version())]
    for k, v in info.items():
        rows.append((k, str(v)))
    lines = ["**What happened?** (please describe)", "", "", "---"]
    lines += [f"- {k}: {v}" for k, v in rows]
    if selftest:
        lines += ["", "Install self-test (install.log):", "```"] + list(selftest) + ["```"]
    if log_lines:
        lines += ["", "Last log lines:", "```"] + list(log_lines) + ["```"]
    return redact("\n".join(lines) + "\n")


def build_url(title: str = "[bug] ", info: dict | None = None, log_lines: list[str] | None = None,
              max_url: int = MAX_URL, selftest: list[str] | None = None) -> str:
    """Issue URL with the body cut (oldest run-log lines first, then the text) so the whole URL stays <= max_url.
    The self-test block is short and comes before the run log, so it survives the cut."""
    logs = list(log_lines or [])
    while True:
        body = build_body(info, logs, selftest)
        url = f"{ISSUE_URL}?title={quote(title)}&body={quote(body)}"
        if len(url) <= max_url:
            return url
        if logs:
            logs = logs[1:]  # drop the oldest log line
            continue
        keep = len(body)
        while keep > 100:
            keep = int(keep * 0.8)
            url = f"{ISSUE_URL}?title={quote(title)}&body={quote(body[:keep] + chr(10) + '...(truncated)')}"
            if len(url) <= max_url:
                return url
        return f"{ISSUE_URL}?title={quote(title)}"


def open_report(info: dict | None = None, say=print) -> str:
    """Open the browser on the pre-filled issue; print the URL when no browser could be opened."""
    merged = dict(env_info())
    merged.update(info or {})
    url = build_url(info=merged, log_lines=last_log_lines(200), selftest=selftest_lines())
    try:
        import webbrowser

        opened = webbrowser.open(url)
    except Exception:  # noqa: BLE001
        opened = False
    if not opened:
        say(url)
    return url


def main(argv=None) -> int:
    open_report(say=lambda m: print(m, file=sys.stderr))
    return 0
