"""'Report a problem': build a pre-filled GitHub new-issue URL and open it in the system browser.

Nothing is uploaded by the app: the user reviews the issue text on GitHub and submits it themselves.
Paths and the user name are redacted from the text before it goes into the URL.
"""
from __future__ import annotations

import getpass
import os
import platform
import re
import subprocess
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


def _cpu_features() -> str:
    """CPU model + AVX2/AVX-512 flags (Linux /proc/cpuinfo; elsewhere just the platform string)."""
    model, flags = platform.processor() or platform.machine(), ""
    try:
        for line in Path("/proc/cpuinfo").read_text("utf-8", errors="replace").splitlines():
            if line.startswith("model name") and ":" in line:
                model = line.split(":", 1)[1].strip()
            elif line.startswith("flags") and ":" in line:
                flags = line.split(":", 1)[1]
                break
    except OSError:
        pass
    fl = set(flags.split())
    feats = [n for n, f in (("AVX2", "avx2"), ("AVX-512", "avx512f")) if f in fl]
    return f"{model} [{', '.join(feats) or 'AVX2/AVX-512 unknown or absent'}]"


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
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"],
                             capture_output=True, text=True, timeout=10).stdout.strip().splitlines()
        info["NVIDIA driver"] = out[0].strip() if out else "unknown"
    except Exception:  # noqa: BLE001
        info["NVIDIA driver"] = "unknown"
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


def last_log_lines(n: int = LOG_LINES) -> list[str]:
    """Last n lines of the newest *.log in the log directory (empty when there is none)."""
    try:
        logs = sorted(userdirs.log_dir().glob("*.log"), key=lambda p: p.stat().st_mtime)
        if not logs:
            return []
        return logs[-1].read_text("utf-8", errors="replace").splitlines()[-n:]
    except OSError:
        return []


def build_body(info: dict | None = None, log_lines: list[str] | None = None) -> str:
    info = dict(info or {})
    rows = [("App version", _version()),
            ("OS", f"{platform.system()} {platform.release()}"),
            ("Python", platform.python_version())]
    for k, v in info.items():
        rows.append((k, str(v)))
    lines = ["**What happened?** (please describe)", "", "", "---"]
    lines += [f"- {k}: {v}" for k, v in rows]
    if log_lines:
        lines += ["", "Last log lines:", "```"] + list(log_lines) + ["```"]
    return redact("\n".join(lines) + "\n")


def build_url(title: str = "[bug] ", info: dict | None = None, log_lines: list[str] | None = None,
              max_url: int = MAX_URL) -> str:
    """Issue URL with the body cut (log lines first, then the text) so the whole URL stays <= max_url."""
    logs = list(log_lines or [])
    while True:
        body = build_body(info, logs)
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
    url = build_url(info=merged, log_lines=last_log_lines(200))
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
