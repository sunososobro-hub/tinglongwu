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

INSTALLERS = {"linux": "scripts/install.sh", "macos": "scripts/mac_install.sh"}


def commands(os_name, directory):
    """(install, setup, tlwu) shell commands for ttfs.measure. Models are skipped in the install step only."""
    script = INSTALLERS[os_name]
    d = shlex.quote(directory)
    env = "TLWU_DIR=%s TLWU_YES=1 TLWU_NO_UPDATE=1 TLWU_UI_LANG=en" % d
    if os_name == "macos":
        env += " TLWU_SKIP_BLACKHOLE=1"
    install = "%s TLWU_SKIP_MODELS=1 bash %s </dev/null" % (env, script)
    setup = "%s bash %s </dev/null" % (env, script)
    tlwu = "%s/.venv/bin/tlwu --file %s --device cpu" % (d, shlex.quote(ttfs.TTFS_CLIP))
    return install, setup, tlwu


def summary_text(os_name, doc):
    def num(k):
        return "n/a" if doc.get(k) is None else "%.1f" % doc[k]
    return ("### ttfs (%s)\n\nttfs_seconds: %s\nexcl_download_seconds: %s (budget %d s, %d Mbps baseline)\n"
            % (os_name, num("ttfs_seconds"), num("excl_download_seconds"), ttfs.TTFS_BUDGET_S, ttfs.BASELINE_MBPS))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--os", required=True, choices=sorted(INSTALLERS))
    ap.add_argument("--dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--summary", default=None)
    a = ap.parse_args(argv)
    install, setup, tlwu = commands(a.os, a.dir)
    rc = ttfs.main(["--install", install, "--setup", setup, "--tlwu", tlwu, "--out", a.out])
    import json
    try:
        with open(a.out, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        doc = {}
    text = summary_text(a.os, doc)
    print(text)
    if a.summary:
        with open(a.summary, "a", encoding="utf-8") as f:
            f.write(text)
    return rc


if __name__ == "__main__":
    sys.exit(main())
