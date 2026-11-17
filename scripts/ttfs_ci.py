#!/usr/bin/env python3
"""CI driver for the install-clean `ttfs` job (#775): time install -> first subtitle on Linux / macOS.

  python scripts/ttfs_ci.py --os linux|macos --dir DIR --out ttfs.json [--summary FILE]

Runs scripts/ttfs.py with (install = installer with TLWU_SKIP_MODELS=1, setup = the same installer re-run with the
models, subtitles = the installed `tlwu` on the bundled clip), then appends `ttfs_seconds` and `excl_download_seconds`
to --summary (the GitHub job summary). It measures only: the number never fails the job; a crash or no subtitle does."""
import argparse
import os
import shlex
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ttfs  # noqa: E402

INSTALLERS = {"linux": "scripts/install.sh", "macos": "scripts/mac_install.sh", "windows": None}


OUTPUT_LOG = "ttfs_output.log"  # #1341: installer output with per-line seconds, next to --out


def print_output_log(path):
    """#1341: the timed installer output in the job log (a folded group on GitHub Actions), so a slow run can be read
    line by line without downloading an artifact."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")   # a console that cannot print a character must not fail the job
    print("::group::installer output with seconds (%s)" % OUTPUT_LOG)
    print(text, end="")
    print("::endgroup::")


def windows_commands(directory, setup_exe):
    """#934: the released wizard run silently into DIRECTORY (cmd.exe commands). `install` is the whole wizard run;
    `setup` prints its install.log (the download lines ttfs.parse_download_bytes reads) and then fetches what the
    wizard left to the app: since v0.4.6 the wizard runs `setup --default-listen --defer-mt` (Whisper only, #1359)
    and the GUI downloads Hy-MT2 in the background (#1358), so the CLI `tlwu` alone stopped with "Hy-MT2 1.8B is not
    installed" (#1416). `setup --default-listen` (no --defer-mt) skips the installed Whisper and fetches Hy-MT2, the
    same download the app makes; on an older wizard that already fetched it, it only re-checks."""
    bs = "\\"
    d = directory.rstrip("\\/")
    install = 'start /wait "" "%s" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /DIR="%s"' % (setup_exe, d)
    # #1060: the released wizard installs the prebuilt core (#771): {app}\tlwu.exe, no .venv. A source-layout install
    # (install.ps1) still has .venv\Scripts\tlwu.exe; run whichever exists.
    core, venv = bs.join([d, "tlwu.exe"]), bs.join([d, ".venv", "Scripts", "tlwu.exe"])
    venv_setup = bs.join([d, ".venv", "Scripts", "tlwu-setup.exe"])
    fetch = 'if exist "%s" ("%s" setup --default-listen) else ("%s" --default-listen)' % (core, core, venv_setup)
    setup = 'type "%s" & %s' % (bs.join([d, "install.log"]), fetch)
    clip = bs.join([d] + ttfs.TTFS_CLIP.split("/"))
    args = '--file "%s" --device cpu' % clip
    tlwu = 'if exist "%s" ("%s" %s) else ("%s" %s)' % (core, core, args, venv, args)
    return install, setup, tlwu


def commands(os_name, directory, setup_exe=None):
    """(install, setup, tlwu) shell commands for ttfs.measure. Models are skipped in the install step only."""
    if os_name == "windows":
        return windows_commands(directory, setup_exe)
    script = INSTALLERS[os_name]
    d = shlex.quote(directory)
    env = "TLWU_DIR=%s TLWU_YES=1 TLWU_NO_UPDATE=1 TLWU_UI_LANG=en" % d
    if os_name == "macos":
        env += " TLWU_SKIP_BLACKHOLE=1"
    install = "%s TLWU_SKIP_MODELS=1 bash %s </dev/null" % (env, script)
    setup = "%s bash %s </dev/null" % (env, script)
    tlwu = "%s/.venv/bin/tlwu --file %s --device cpu" % (d, shlex.quote(ttfs.TTFS_CLIP))
    return install, setup, tlwu


def dir_size_mb(directory):
    """#773: total size of DIRECTORY on disk in MB (0.0 when it does not exist)."""
    total = 0
    for root, _dirs, files in os.walk(directory):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return round(total / (1024 * 1024), 1)


def summary_text(os_name, doc, install_mb=None):
    def num(k):
        return "n/a" if doc.get(k) is None else "%.1f" % doc[k]
    text = ("### ttfs (%s)\n\nttfs_seconds: %s\nexcl_download_seconds: %s (budget %d s, %d Mbps baseline)\n"
            % (os_name, num("ttfs_seconds"), num("excl_download_seconds"), ttfs.TTFS_BUDGET_S, ttfs.BASELINE_MBPS))
    if doc.get("first_source_subtitle_baseline_seconds") is not None:   # #1360
        # #1416: the windows setup step fetches Hy-MT2 before the timer, so its raw value includes that download
        raw = "includes the Hy-MT2 fetch in setup" if os_name == "windows" else "Hy-MT2 deferred"
        text += ("first_source_subtitle_seconds: %s (%s; %s at the %d Mbps baseline, only Whisper downloaded, "
                 "budget %d s)\n" % (num("first_source_subtitle_seconds"), raw,
                                     num("first_source_subtitle_baseline_seconds"),
                                     ttfs.BASELINE_MBPS, ttfs.TTFS_BUDGET_S))
    if install_mb is not None:
        text += "install_size_mb: %.1f\n" % install_mb
    return text + phase_text(doc)


def phase_text(doc):
    """#1341: where the seconds go, as text for the job log and summary: the three measured parts, then per installer
    run its steps (`==> ` lines) and the output lines that were followed by the longest wait. "" when DOC has none."""
    if not any(doc.get(k) for k in ("install_phases", "setup_phases")):
        return ""

    def num(k):
        return "n/a" if doc.get(k) is None else "%.1f" % doc[k]
    out = ["", "install_seconds: %s  setup_seconds: %s  first_subtitle_seconds: %s"
           % (num("install_seconds"), num("setup_seconds"), num("first_subtitle_seconds"))]
    for key, title in (("install_phases", "install (models skipped)"), ("setup_phases", "setup (with models)")):
        rep = doc.get(key) or {}
        out.append("phases, %s:" % title)
        out += ["  %7.1f s  %s" % (p["seconds"], p["name"]) for p in rep.get("phases", [])]
        if rep.get("slow_lines"):
            out.append("slowest lines, %s (seconds until the next output line):" % title)
            out += ["  %7.1f s  %s" % (r["seconds"], r["line"]) for r in rep["slow_lines"]]
    return "\n".join(out) + "\n"


def setup_time_report(directory, install_seconds, out_path):
    """#773: the wizard run on this clean VM judged like `clean_machine_check.py setup-time`. Writes setup_time.json
    (setup_seconds, download_seconds, download_mb, pip_lines, ready) next to ttfs.json and returns summary text."""
    import json
    sys.path.insert(0, os.path.join(os.path.dirname(HERE), "packaging"))
    import clean_machine_check as cmc
    log_path = os.path.join(directory, "install.log")
    try:
        with open(log_path, encoding="utf-8-sig", errors="replace") as f:
            log = f.read()
    except OSError:
        log = ""
    res = cmc.build_result(install_seconds, log)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(res, f, indent=2)
    body = json.dumps(res, indent=2)
    lines = "\n".join(cmc.judge(res))
    return "### setup_time (windows)\n\n```json\n%s\n```\n%s\n" % (body, lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--os", required=True, choices=sorted(INSTALLERS))
    ap.add_argument("--dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--summary", default=None)
    ap.add_argument("--setup-exe", default=None, help="windows: path of the released tinglongwu-setup.exe")
    a = ap.parse_args(argv)
    if a.os == "windows" and not a.setup_exe:
        ap.error("--setup-exe is required for --os windows")
    install, setup, tlwu = commands(a.os, a.dir, a.setup_exe)
    log = os.path.join(os.path.dirname(os.path.abspath(a.out)), OUTPUT_LOG)
    rc = ttfs.main(["--install", install, "--setup", setup, "--tlwu", tlwu, "--out", a.out, "--log", log])
    print_output_log(log)
    import json
    try:
        with open(a.out, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        doc = {}
    if doc.get("download_seconds") is not None:   # #934: download_mb implied by the baseline speed
        doc["download_mb"] = round(doc["download_seconds"] * ttfs.BASELINE_MBPS / 8, 1)
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=2)
    text = summary_text(a.os, doc, dir_size_mb(a.dir))
    if a.os == "windows" and doc.get("install_seconds") is not None:
        text += setup_time_report(a.dir, doc["install_seconds"],
                                  os.path.join(os.path.dirname(os.path.abspath(a.out)), "setup_time.json"))
    print(text)
    if a.summary:
        with open(a.summary, "a", encoding="utf-8") as f:
            f.write(text)
    return rc


if __name__ == "__main__":
    sys.exit(main())
