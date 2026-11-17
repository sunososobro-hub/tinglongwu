"""Windows self-update for wizard installs (#698). Networked, so it lives here and runs only as a child process.

  --check CURRENT [--count]
                         ask GitHub for the latest release (6 s, any failure = "SKIP <why>"); prints one line:
                         "UPDATE X.Y.Z" when it is newer than CURRENT, "NONE" otherwise. Always exit code 0.
                         --count (only the once-a-day automatic check, #806) asks the anonymous counter Worker
                         (cloudflare/update-counter) instead: it returns the same GitHub answer and adds 1 to a count
                         keyed only by (date, app version, OS). Worker unreachable -> GitHub API exactly as without it.
  --download DIR         download tinglongwu-setup.exe of the latest release into DIR (resumable, see resume.fetch),
                         verify it against the release's SHA256SUMS.txt; prints "PROGRESS n" lines then
                         "READY <path>". Exit 1 with "FAIL <why>" (a SHA256 mismatch aborts, the file is deleted).
                         #1348: with SUMS_PUBKEY set, SHA256SUMS.txt must carry a valid Ed25519 signature
                         (SHA256SUMS.txt.sig) or nothing is downloaded. The verified sums (+ signature) are kept next
                         to the download as <file>.sums / <file>.sums.sig for the re-check before apply.
  --apply EXE --app DIR --pid N
                         re-check EXE against its kept sums (signature + SHA256, #1348; "FAIL <why>", exit 1, nothing
                         runs), then start a detached PowerShell helper that waits for process N (the GUI) to exit,
                         re-hashes EXE once more, runs it silently, then starts the GUI again. The installer keeps
                         models\\ and the user config.

  --plan --app DIR       (#946) app-layer-only update? Compares DIR/runtime-layer.json (key, lite) with the latest
                         release's tinglongwu-app-layer.zip.manifest.json. Prints "PLAN app <MB>" when they match,
                         else (or on any problem) "PLAN full <MB>". Always exit code 0.
  --download-app DIR     download tinglongwu-app-layer.zip into DIR (resumable, sha256 must match both
                         SHA256SUMS.txt and the manifest); "PROGRESS n" lines then "READY <path>" or "FAIL <why>".
  --apply-app ZIP --app DIR --pid N
                         detached PowerShell helper: wait for the GUI, extract ZIP to DIR\\.update-staging, rename the
                         current app-layer items to *.old, move the staged ones in, start tlwu.exe. Any error
                         restores the *.old set and starts the old GUI. models\\ and the config are never touched.

The GUI (tinglongwu/gui/autoupdate.py) decides whether to ask at all (auto_update=1, once per 24 h, never with
TLWU_OFFLINE=1). Pre-releases and drafts are ignored. Run: `python -m tinglongwu_setup.selfupdate ...`.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

from . import ed25519, resume

REPO = "sunososobro-hub/tinglongwu"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
API_TAG = f"https://api.github.com/repos/{REPO}/releases/tags/v{{version}}"  # #801: one version's release
EXE_NAME = "tinglongwu-setup.exe"
SUMS_NAME = "SHA256SUMS.txt"
SIG_NAME = SUMS_NAME + ".sig"  # #1348: base64 of the 64-byte Ed25519 signature of SHA256SUMS.txt's exact bytes
# #1348: hex Ed25519 public key of the release signer (scripts/sign_sums.py keygen; the secret half is the repo secret
# TLWU_SUMS_SIGNING_KEY used by release.yml). Empty = signatures are not required yet (releases made before the key
# existed); set = an unsigned or wrongly signed SHA256SUMS.txt is refused. Never read from the environment.
SUMS_PUBKEY = ""
APP_ZIP_NAME = "tinglongwu-app-layer.zip"  # #946: the app layer alone (tinglongwu/, tlwu_app/, tinglongwu_setup/ ...)
APP_MANIFEST_NAME = APP_ZIP_NAME + ".manifest.json"  # {name, size, sha256, url, runtime_key, lite}
RUNTIME_MANIFEST = "runtime-layer.json"  # in the install dir: {key, lite, ...}
APP_ITEMS = ("tinglongwu", "tlwu_app", "tinglongwu_setup", "app-layer.json", "tlwu.exe")  # swapped by --apply-app
STAGING_DIR = ".update-staging"
CHECK_TIMEOUT_S = 6
SUMS_TIMEOUT_S = 30
WAIT_GUI_EXIT_S = 120  # the helper gives the GUI this long to close before installing anyway
INSTALLER_ARGS = ("/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-")  # /SILENT: progress window, no questions
# #806 counter Worker base URL, set once the Worker is deployed (cloudflare/update-counter/README.md). Empty = no
# counter, every check goes straight to GitHub. TLWU_UPDATE_COUNTER overrides it (tests, staging).
COUNTER_BASE = ""
COUNTER_TIMEOUT_S = 4  # short, so a dead Worker still leaves time for the GitHub fallback
_UA = {"User-Agent": "tlwu-setup/0.1", "Accept": "application/vnd.github+json"}
_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


def parse_version(tag: str | None) -> tuple[int, int, int] | None:
    """'v0.3.3' / '0.3.3' -> (0, 3, 3). Anything else (pre-release suffix, garbage) -> None."""
    m = _VERSION_RE.match((tag or "").strip())
    return tuple(int(x) for x in m.groups()) if m else None  # type: ignore[return-value]


def is_newer(latest: str | None, current: str | None) -> bool:
    new, cur = parse_version(latest), parse_version(current)
    return bool(new and cur and new > cur)


def _get(url: str, timeout: int = CHECK_TIMEOUT_S) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers=_UA), timeout=timeout) as r:
        return r.read()


def os_tag(platform: str | None = None) -> str:
    p = platform or sys.platform
    return "windows" if p == "win32" else "mac" if p == "darwin" else "linux" if p.startswith("linux") else "other"


def counter_url(current: str, env=None, platform: str | None = None) -> str | None:
    """The Worker's /v1/latest URL carrying only the app version and the OS, or None when no counter is set."""
    env = os.environ if env is None else env
    base = (env.get("TLWU_UPDATE_COUNTER") or COUNTER_BASE).rstrip("/")
    if not base.startswith("https://"):
        return None
    ver = current if parse_version(current) else "other"
    return f"{base}/v1/latest?" + urllib.parse.urlencode({"v": ver, "os": os_tag(platform)})


def _latest_json(get, count_url: str | None) -> dict:
    if count_url:
        try:
            rel = json.loads(get(count_url, timeout=COUNTER_TIMEOUT_S).decode("utf-8"))
            if isinstance(rel, dict) and isinstance(rel.get("tag_name"), str) and rel["tag_name"]:
                return rel  # anything else ({} / [] / an error object) -> GitHub below
        except Exception:  # noqa: BLE001  # Worker down / blocked / bad answer: ask GitHub as before
            pass
    return json.loads(get(API_LATEST).decode("utf-8"))


def latest_release(get=_get, count_url: str | None = None) -> dict | None:
    """{'version', 'exe_url', 'sums_url'} of the latest published release, or None (pre-release, draft, no wizard
    asset, unparseable tag). Network errors propagate; callers turn them into a silent skip. count_url: ask the
    counter Worker first (check only; download() always reads GitHub, so the Worker never picks what is installed)."""
    rel = _latest_json(get, count_url)
    if rel.get("prerelease") or rel.get("draft"):
        return None
    tag = rel.get("tag_name") or ""
    if parse_version(tag) is None:
        return None
    assets = {a.get("name"): a.get("browser_download_url") for a in rel.get("assets") or []}
    if not assets.get(EXE_NAME) or not assets.get(SUMS_NAME):
        return None
    sizes = {a.get("name"): a.get("size") for a in rel.get("assets") or []}
    return {"version": tag.lstrip("v"), "exe_url": assets[EXE_NAME], "sums_url": assets[SUMS_NAME],
            "sig_url": assets.get(SIG_NAME),
            "exe_size": sizes.get(EXE_NAME), "app_url": assets.get(APP_ZIP_NAME),
            "app_manifest_url": assets.get(APP_MANIFEST_NAME)}


def parse_sums(text: str, name: str) -> str | None:
    """SHA256 of `name` from a `sha256sum` listing ('<hex>  <name>' or '<hex> *<name>'), lower case, or None."""
    for line in text.splitlines():
        parts = line.strip().split()
        if len(parts) == 2 and parts[1].lstrip("*") == name and re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
            return parts[0].lower()
    return None


def parse_sig(data: bytes) -> bytes | None:
    """The 64 signature bytes of a SHA256SUMS.txt.sig (base64, surrounding whitespace allowed), or None."""
    try:
        sig = base64.b64decode(data.strip(), validate=True)
    except (ValueError, TypeError):
        return None
    return sig if len(sig) == 64 else None


def verify_sums(sums: bytes, sig: bytes | None, pubkey: str | None = None) -> None:
    """#1348: ValueError unless `sig` is a valid signature of `sums` by the embedded key. No key = no check."""
    key = SUMS_PUBKEY if pubkey is None else pubkey
    if not key:
        return
    raw = parse_sig(sig) if sig is not None else None
    if raw is None:
        raise ValueError(f"{SUMS_NAME} is not signed ({SIG_NAME} missing or malformed)")
    if not ed25519.verify(bytes.fromhex(key), sums, raw):
        raise ValueError(f"{SUMS_NAME} signature is not valid")


def fetch_sums(rel: dict, get=_get) -> tuple[bytes, bytes | None]:
    """(SHA256SUMS.txt bytes, signature file bytes or None), already verified (verify_sums)."""
    sums = get(rel["sums_url"], timeout=SUMS_TIMEOUT_S)
    sig = get(rel["sig_url"], timeout=SUMS_TIMEOUT_S) if rel.get("sig_url") else None
    verify_sums(sums, sig)
    return sums, sig


def keep_sums(dest: Path, sums: bytes, sig: bytes | None) -> None:
    """Store the verified sums (and signature) beside the download, for verify_pending."""
    Path(str(dest) + ".sums").write_bytes(sums)
    sig_path = Path(str(dest) + ".sums.sig")
    if sig is not None:
        sig_path.write_bytes(sig)
    else:
        sig_path.unlink(missing_ok=True)


def verify_pending(path: Path, pubkey: str | None = None) -> str:
    """#1348: re-check a downloaded installer / app-layer zip right before apply. Returns its SHA256; ValueError when
    the kept sums are missing, their signature fails (key set) or the file no longer matches."""
    path = Path(path)
    name = APP_ZIP_NAME if path.suffix.lower() == ".zip" else EXE_NAME
    try:
        sums = Path(str(path) + ".sums").read_bytes()
    except OSError:
        raise ValueError(f"no kept {SUMS_NAME} for {path.name}: download it again") from None
    sig_path = Path(str(path) + ".sums.sig")
    verify_sums(sums, sig_path.read_bytes() if sig_path.exists() else None, pubkey)
    want = parse_sums(sums.decode("utf-8", "replace"), name)
    if not want:
        raise ValueError(f"{name} is not listed in the kept {SUMS_NAME}")
    if resume._sha(path) != want:
        raise ValueError(f"{path.name} was changed after download (SHA256 mismatch)")
    return want


def check(current: str, get=_get, env=None, count: bool = False) -> str:
    """The one line --check prints. count=True only for the automatic daily check (auto_update=1)."""
    env = os.environ if env is None else env
    if env.get("TLWU_OFFLINE") == "1":
        return "SKIP offline"
    try:
        rel = latest_release(get, counter_url(current, env) if count else None)
    except Exception as e:  # noqa: BLE001  # timeout, HTTP error, bad JSON: never bother the user
        return f"SKIP {type(e).__name__}"
    if rel is None:
        return "NONE"
    return f"UPDATE {rel['version']}" if is_newer(rel["version"], current) else "NONE"


def download(dest_dir: Path, get=_get, fetch=resume.fetch, say=print) -> Path:
    """Download + verify the wizard of the latest release. ValueError on a missing/mismatching checksum."""
    rel = latest_release(get)
    if rel is None:
        raise ValueError("no published release with a setup wizard")
    sums, sig = fetch_sums(rel, get)
    sha = parse_sums(sums.decode("utf-8", "replace"), EXE_NAME)
    if not sha:
        raise ValueError(f"{EXE_NAME} is not listed in {SUMS_NAME}")
    dest = Path(dest_dir) / f"tinglongwu-setup-{rel['version']}.exe"
    dest.parent.mkdir(parents=True, exist_ok=True)
    keep_sums(dest, sums, sig)
    total = [0]

    def on_bytes(n: int) -> None:
        pct = min(99, n * 100 // total[0]) if total[0] else 0
        if pct != getattr(on_bytes, "last", -1):
            on_bytes.last = pct  # type: ignore[attr-defined]
            say(f"PROGRESS {pct}")

    def opener(url, start):
        r = resume._open_range(url, start)
        try:
            total[0] = start + int(r.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            total[0] = 0
        return r

    if fetch is resume.fetch:
        return fetch(rel["exe_url"], dest, sha, opener=opener, on_bytes=on_bytes)
    return fetch(rel["exe_url"], dest, sha, on_bytes=on_bytes)


def _mb(n) -> int:
    try:
        return max(0, round(int(n) / (1 << 20)))
    except (TypeError, ValueError):
        return 0


def _read_runtime(app_dir: Path) -> dict:
    try:
        data = json.loads((Path(app_dir) / RUNTIME_MANIFEST).read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def app_manifest(rel: dict, get=_get) -> dict | None:
    """The release's app-layer manifest, or None when it is missing or invalid."""
    if not rel.get("app_url") or not rel.get("app_manifest_url"):
        return None
    try:
        man = json.loads(get(rel["app_manifest_url"], timeout=SUMS_TIMEOUT_S).decode("utf-8"))
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(man, dict) or not isinstance(man.get("runtime_key"), str) or not man["runtime_key"]:
        return None
    if not isinstance(man.get("lite"), bool) or not re.fullmatch(r"[0-9a-fA-F]{64}", str(man.get("sha256") or "")):
        return None
    return man


def plan(app_dir: Path, get=_get) -> str:
    """'PLAN app <MB>' when only the app layer has to be downloaded, else 'PLAN full <MB>' (never raises)."""
    full = "PLAN full 0"
    try:
        rel = latest_release(get)
        if rel is None:
            return full
        full = f"PLAN full {_mb(rel.get('exe_size'))}"
        local, man = _read_runtime(app_dir), app_manifest(rel, get)
        if not man or not local.get("key") or local["key"] != man["runtime_key"] or local.get("lite") != man["lite"]:
            return full
        return f"PLAN app {_mb(man.get('size'))}"
    except Exception:  # noqa: BLE001  # network error etc.: the full installer, as before
        return full


def download_app(dest_dir: Path, get=_get, fetch=resume.fetch, say=print) -> Path:
    """Download + verify the app-layer zip. ValueError when it is missing or the checksums disagree; a file whose
    sha256 is wrong is deleted."""
    rel = latest_release(get)
    man = app_manifest(rel, get) if rel else None
    if not rel or not man:
        raise ValueError("no app-layer asset in the latest release")
    sums, sig = fetch_sums(rel, get)
    sha = parse_sums(sums.decode("utf-8", "replace"), APP_ZIP_NAME)
    if not sha or sha != str(man["sha256"]).lower():
        raise ValueError(f"{APP_ZIP_NAME} checksum missing or not equal in {SUMS_NAME} and the manifest")
    dest = Path(dest_dir) / f"tinglongwu-app-layer-{rel['version']}.zip"
    dest.parent.mkdir(parents=True, exist_ok=True)
    keep_sums(dest, sums, sig)
    total = [0]

    def on_bytes(n: int) -> None:
        pct = min(99, n * 100 // total[0]) if total[0] else 0
        if pct != getattr(on_bytes, "last", -1):
            on_bytes.last = pct  # type: ignore[attr-defined]
            say(f"PROGRESS {pct}")

    def opener(url, start):
        r = resume._open_range(url, start)
        try:
            total[0] = start + int(r.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            total[0] = 0
        return r

    try:
        if fetch is resume.fetch:
            return fetch(rel["app_url"], dest, sha, opener=opener, on_bytes=on_bytes)
        return fetch(rel["app_url"], dest, sha, on_bytes=on_bytes)
    except ValueError:  # sha256 mismatch: no corrupt file is left behind
        for f in (dest, dest.with_name(dest.name + ".part")):
            f.unlink(missing_ok=True)
        raise


def _ps_quote(s: str) -> str:
    return "'" + str(s).replace("'", "''") + "'"


def _ps_hash_guard(path_expr: str, sha: str | None) -> str:
    """#1348: PowerShell line that stops the helper when the file's SHA256 changed since verify_pending."""
    if not sha:
        return ""
    return (f"if ((Get-FileHash -Algorithm SHA256 -LiteralPath {path_expr}).Hash.ToLower() -ne {_ps_quote(sha.lower())})"
            " { throw 'update file changed after verification' }")


def apply_script(exe: Path, app_dir: Path, pid: int, sha: str | None = None) -> str:
    """PowerShell for the helper: wait for the GUI, install silently, start the GUI again (also after a failed
    install, so the user is never left without the app). The prebuilt core's tlwu.exe (#768) wins over the source
    install's .venv GUI launcher."""
    core = Path(app_dir) / "tlwu.exe"
    gui = Path(app_dir) / ".venv" / "Scripts" / "tlwu-gui.exe"
    log = Path(exe).with_suffix(".log")
    args = ", ".join(_ps_quote(a) for a in (*INSTALLER_ARGS, f"/LOG={log}"))
    wd = _ps_quote(app_dir)
    guard = _ps_hash_guard(_ps_quote(exe), sha)
    run = f"Start-Process -FilePath {_ps_quote(exe)} -ArgumentList @({args}) -Wait"
    return "\n".join([
        "$ErrorActionPreference = 'SilentlyContinue'",
        f"Wait-Process -Id {int(pid)} -Timeout {WAIT_GUI_EXIT_S}",
        f"try {{ {guard} ; {run} }} catch {{ }}" if guard else run,  # a changed file is never run; the GUI restarts
        f"if (Test-Path {_ps_quote(core)}) {{ Start-Process -FilePath {_ps_quote(core)} -WorkingDirectory {wd} }}",
        f"elseif (Test-Path {_ps_quote(gui)}) {{ Start-Process -FilePath {_ps_quote(gui)} -WorkingDirectory {wd} }}",
        "",
    ])


def apply_app_script(zip_path: Path, app_dir: Path, pid: int, sha: str | None = None) -> str:
    """PowerShell for the app-layer swap (#946): wait for the GUI, extract to <app>\\.update-staging, rename the
    current items to *.old, move the staged ones in, start tlwu.exe. Any error: put the *.old set back and start the
    old GUI (the user is never left without the app). *.old is deleted by the next successful start."""
    items = ", ".join(_ps_quote(i) for i in APP_ITEMS)
    return "\n".join([
        "$ErrorActionPreference = 'Stop'",
        f"$app = {_ps_quote(app_dir)}",
        f"$zip = {_ps_quote(zip_path)}",
        f"$stage = Join-Path $app {_ps_quote(STAGING_DIR)}",
        f"$items = @({items})",
        "$moved = @()",
        f"Wait-Process -Id {int(pid)} -Timeout {WAIT_GUI_EXIT_S} -ErrorAction SilentlyContinue",
        "try {",
        *(["  " + _ps_hash_guard("$zip", sha)] if sha else []),
        "  if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }",
        "  Expand-Archive -LiteralPath $zip -DestinationPath $stage -Force",
        "  foreach ($i in $items) { if (-not (Test-Path -LiteralPath (Join-Path $stage $i))) { throw \"missing $i\" } }",
        "  foreach ($i in $items) {",
        "    $cur = Join-Path $app $i; $old = $cur + '.old'",
        "    if (Test-Path -LiteralPath $old) { Remove-Item -LiteralPath $old -Recurse -Force }",
        "    if (Test-Path -LiteralPath $cur) { Rename-Item -LiteralPath $cur -NewName ($i + '.old') }",
        "    $moved += $i  # only after the rename worked: a failed rename must not make the catch delete the original",
        "    Move-Item -LiteralPath (Join-Path $stage $i) -Destination $cur",
        "  }",
        "  Remove-Item -LiteralPath $stage -Recurse -Force",
        "} catch {",
        "  foreach ($i in $moved) {",
        "    $cur = Join-Path $app $i; $old = $cur + '.old'",
        "    if (Test-Path -LiteralPath $old) {",
        "      if (Test-Path -LiteralPath $cur) { Remove-Item -LiteralPath $cur -Recurse -Force -ErrorAction SilentlyContinue }",
        "      Rename-Item -LiteralPath $old -NewName $i -ErrorAction SilentlyContinue",
        "    } elseif (Test-Path -LiteralPath $cur) { Remove-Item -LiteralPath $cur -Recurse -Force -ErrorAction SilentlyContinue }",
        "  }",
        "  Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue",
        "}",
        "$exe = Join-Path $app 'tlwu.exe'",
        "if (Test-Path -LiteralPath $exe) { Start-Process -FilePath $exe -WorkingDirectory $app }",
        "",
    ])


def launch_apply(exe: Path, app_dir: Path, pid: int, popen=subprocess.Popen, app_layer: bool = False,
                 sha: str | None = None) -> Path:
    script = Path(exe).with_name("tlwu-apply-update.ps1")
    text = apply_app_script(exe, app_dir, pid, sha) if app_layer else apply_script(exe, app_dir, pid, sha)
    script.write_text(text, "utf-8-sig")
    cmd = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-File", str(script)]
    base = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    breakaway = getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0)
    try:  # leave the GUI's job object (if any) so closing the GUI does not kill the helper
        popen(cmd, creationflags=base | breakaway, close_fds=True)
    except OSError:
        popen(cmd, creationflags=base, close_fds=True)
    return script


def main(argv=None) -> int:
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    p = argparse.ArgumentParser(prog="python -m tinglongwu_setup.selfupdate")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", metavar="CURRENT")
    g.add_argument("--download", metavar="DIR")
    g.add_argument("--apply", metavar="EXE")
    g.add_argument("--plan", action="store_true", help="app layer only, or the full installer? (needs --app)")
    g.add_argument("--download-app", metavar="DIR")
    g.add_argument("--apply-app", metavar="ZIP")
    p.add_argument("--count", action="store_true", help="automatic daily check: go through the counter Worker")
    p.add_argument("--app", metavar="DIR")
    p.add_argument("--pid", type=int)
    a = p.parse_args(argv)
    if a.check:
        print(check(a.check, count=a.count))
        return 0
    if a.download:
        if os.environ.get("TLWU_OFFLINE") == "1":
            print("FAIL offline (TLWU_OFFLINE=1)")
            return 1
        try:
            path = download(Path(a.download))
        except Exception as e:  # noqa: BLE001
            print(f"FAIL {type(e).__name__}: {e}"[:200])
            return 1
        print(f"READY {path}")
        return 0
    if a.plan:
        if not a.app:
            p.error("--plan needs --app")
        print("PLAN full 0" if os.environ.get("TLWU_OFFLINE") == "1" else plan(Path(a.app)))
        return 0
    if a.download_app:
        if os.environ.get("TLWU_OFFLINE") == "1":
            print("FAIL offline (TLWU_OFFLINE=1)")
            return 1
        try:
            path = download_app(Path(a.download_app))
        except Exception as e:  # noqa: BLE001
            print(f"FAIL {type(e).__name__}: {e}"[:200])
            return 1
        print(f"READY {path}")
        return 0
    if not a.app or not a.pid:
        p.error("--apply / --apply-app need --app and --pid")
    target = Path(a.apply_app or a.apply)
    try:
        sha = verify_pending(target)  # #1348: nothing runs unless the file still matches its (signed) sums
    except ValueError as e:
        print(f"FAIL {e}"[:200])
        return 1
    launch_apply(target, Path(a.app), a.pid, app_layer=bool(a.apply_app), sha=sha)
    print("APPLYING")
    return 0


if __name__ == "__main__":
    sys.exit(main())
