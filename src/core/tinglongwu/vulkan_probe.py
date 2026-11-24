"""CPU vs Vulkan speed check (#1773, parent #1735). Pure logic and cache; the timing itself is passed in.

A Vulkan pack is not always faster: on a UMA iGPU it can lose to the CPU (#1735 owner data). So once after the pack
is installed, the app is updated or the GPU driver changes, the session times ASR + MT on the bundled clip on the
CPU and on Vulkan (flow/backend_probe.py, one child process each) and keeps the faster one. The answer is cached
per app version + driver id + pack stamp, so it never runs on every start. Rules (owner 2026-11-23 20:45):
- Vulkan faster (or equal) -> both stages stay on Vulkan; CPU faster -> CPU, one line with both timings.
- The probe fails or times out -> Vulkan is kept and the line says so (a failed probe never silently means CPU).
- TLWU_DEVICE=cpu|vulkan|cuda overrides the probe: nothing is timed and the cached answer is ignored.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

PROBE_FILE = "vulkan_probe.json"
FORCE_VALUES = ("cpu", "vulkan", "cuda")
_ICD_DIRS = ("/usr/share/vulkan/icd.d", "/usr/local/share/vulkan/icd.d", "/etc/vulkan/icd.d")


@dataclass(frozen=True)
class ProbeResult:
    winner: str  # "vulkan" | "cpu"
    cpu_s: float | None  # ASR + MT seconds on the clip; None = not measured
    vulkan_s: float | None
    note: str  # the one line shown to the user
    error: str = ""


_session: dict = {}  # #1783: this process's decision {"key", "winner"}; one decision for ASR and MT, even if the cache write failed


def forced(env=None) -> str:
    """The TLWU_DEVICE override ("" = none)."""
    v = ((os.environ if env is None else env).get("TLWU_DEVICE") or "").strip().lower()
    return v if v in FORCE_VALUES else ""


def cache_path() -> Path:
    from .settings import settings_path

    return settings_path().parent / PROBE_FILE


def _stamp(p: Path) -> str:
    try:
        st = p.stat()
        return f"{p}:{st.st_size}:{st.st_mtime_ns}"
    except OSError:
        return ""


def _icd_files(env, platform: str, home: Path) -> list[Path]:
    """Vulkan ICD manifests (one per installed driver). Windows: listed in the registry; Linux: the icd.d folders."""
    named = env.get("VK_DRIVER_FILES") or env.get("VK_ICD_FILENAMES")
    if named:
        return [Path(p) for p in named.split(os.pathsep) if p]
    if platform == "win32":
        try:
            import winreg

            out = []
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Khronos\Vulkan\Drivers") as k:
                i = 0
                while True:
                    try:
                        out.append(Path(winreg.EnumValue(k, i)[0]))
                    except OSError:
                        break
                    i += 1
            return out
        except OSError:
            return []
    dirs = [Path(d) for d in _ICD_DIRS] + [home / ".local/share/vulkan/icd.d"]
    return sorted(f for d in dirs if d.is_dir() for f in d.glob("*.json"))


def driver_id(env=None, platform: str | None = None, home: Path | None = None, system32: Path | None = None) -> str:
    """Short hash that changes when a Vulkan driver is installed, removed or updated: each ICD manifest and the
    driver library it names (size + mtime), plus the Windows vulkan-1.dll. "" = nothing found."""
    env = os.environ if env is None else env
    platform = platform or sys.platform
    parts = []
    for f in _icd_files(env, platform, home or Path.home()):
        parts.append(_stamp(f))
        try:
            lib = json.loads(f.read_text("utf-8")).get("ICD", {}).get("library_path", "")
        except (OSError, ValueError, AttributeError):
            lib = ""
        if lib:
            lp = Path(lib) if Path(lib).is_absolute() else f.parent / lib
            parts.append(_stamp(lp))
    if platform == "win32":
        parts.append(_stamp((system32 or Path(env.get("SystemRoot", r"C:\Windows")) / "System32") / "vulkan-1.dll"))
    parts = [p for p in parts if p]
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:16] if parts else ""


def cache_key(pack_dir: Path | None, *, version: str | None = None, driver=None) -> dict:
    """App version + driver id + pack stamp (a re-installed pack rewrites version.txt, so it is timed again)."""
    if version is None:
        from . import __version__ as version
    from .mt_backend import VERSION_FILE

    pack = _stamp(Path(pack_dir) / VERSION_FILE) if pack_dir else ""
    return {"app": version, "driver": (driver or driver_id)(), "pack": pack}


def decide(cpu_s: float | None, vulkan_s: float | None, error: str = "") -> ProbeResult:
    if error or cpu_s is None or vulkan_s is None:
        why = error or "no timing"
        return ProbeResult("vulkan", cpu_s, vulkan_s,
                           f"tlwu: GPU speed check failed ({why[:160]}): keeping Vulkan. "
                           "Set TLWU_DEVICE=cpu if the CPU is faster on this machine.", why)
    if vulkan_s <= cpu_s:
        return ProbeResult("vulkan", cpu_s, vulkan_s,
                           f"tlwu: GPU speed check: Vulkan {vulkan_s:.2f} s vs CPU {cpu_s:.2f} s: using Vulkan")
    return ProbeResult("cpu", cpu_s, vulkan_s,
                       f"tlwu: GPU speed check: CPU {cpu_s:.2f} s vs Vulkan {vulkan_s:.2f} s: using CPU "
                       "(Vulkan is slower on this machine; TLWU_DEVICE=vulkan forces it)")


def load(key: dict, path: Path | None = None) -> ProbeResult | None:
    """The cached result for exactly this key, else None (other version / driver / pack, missing or corrupt)."""
    try:
        d = json.loads(Path(path or cache_path()).read_text("utf-8"))
        if d.get("key") != key or d.get("winner") not in ("vulkan", "cpu"):
            return None
        r = d["result"]
        return ProbeResult(r["winner"], r.get("cpu_s"), r.get("vulkan_s"), str(r.get("note", "")),
                           str(r.get("error", "")))
    except Exception:  # noqa: BLE001 - no cache = time again
        return None


def last(path: Path | None = None) -> ProbeResult | None:
    """The last result whatever its key (for diagnostics: `tlwu --probe`, #1772 --check-gpu)."""
    try:
        r = json.loads(Path(path or cache_path()).read_text("utf-8"))["result"]
        return ProbeResult(r["winner"], r.get("cpu_s"), r.get("vulkan_s"), str(r.get("note", "")),
                           str(r.get("error", "")))
    except Exception:  # noqa: BLE001
        return None


def summary(path: Path | None = None) -> str:
    r = last(path)
    return f"last GPU speed check: {r.note.removeprefix('tlwu: ')}" if r else "last GPU speed check: none yet"


def save(key: dict, r: ProbeResult, path: Path | None = None) -> bool:
    try:
        p = Path(path or cache_path())
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"key": key, "winner": r.winner, "result": asdict(r)}, indent=1), "utf-8")
        return True
    except OSError:
        return False


def vulkan_allowed(env=None, key: dict | None = None, path: Path | None = None) -> bool:
    """False only when the cached check for this version / driver / pack found the CPU faster and TLWU_DEVICE
    does not override it. Reads the cache only, never times anything."""
    if forced(env):
        return True
    try:
        from .mt_backend import vulkan_pack_dir

        key = key if key is not None else cache_key(vulkan_pack_dir(warn=lambda _m: None))
        if _session.get("key") == key:  # #1783: the decision made in this session wins over the file
            return _session["winner"] != "cpu"
        r = load(key, path)
    except Exception:  # noqa: BLE001 - a broken cache never turns Vulkan off
        return True
    return not (r is not None and r.winner == "cpu")


def ensure(time_backend, key: dict, *, env=None, path: Path | None = None, say=print) -> ProbeResult | None:
    """Cached result for `key`, else time both backends with `time_backend(backend) -> seconds`, decide, cache and
    say the line. None when TLWU_DEVICE overrides the probe. Never raises."""
    f = forced(env)
    if f:
        say(f"tlwu: TLWU_DEVICE={f}: GPU speed check skipped")
        return None
    hit = load(key, path)
    if hit is not None:
        _session.update(key=dict(key), winner=hit.winner)
        return hit
    times, err = {}, ""
    for b in ("cpu", "vulkan"):
        try:
            times[b] = float(time_backend(b))
        except Exception as e:  # noqa: BLE001 - timeout, crash, a model missing: keep Vulkan (owner rule)
            if type(e).__name__ == "ProbeStopped":  # #1783: session stopped: no result, nothing cached
                return None
            err = f"{b}: {type(e).__name__}: {e}"
            break
    r = decide(times.get("cpu"), times.get("vulkan"), err)
    _session.update(key=dict(key), winner=r.winner)
    save(key, r, path)
    say(r.note)
    return r
