#!/usr/bin/env python3
"""Mobile release-lane decision (#1598). Pure rules, no network.

Given the gate results on a `release/vX.Y.Z` snapshot, decide whether the mobile good tag
`mobile-vX.Y.Z` is created and which Play track receives the AAB. The desktop lanes are
independent: this script never looks at, and never blocks, `vX.Y.Z`.

    python3 -I scripts/mobile_lane.py --branch release/v0.4.11 --analyze success --test success \
        --aab success [--dry-run]
prints one JSON object; exit 0 = tag + production *draft* upload, 1 = skipped (not verified).
Only status `draft` is ever used: the owner presses "start rollout" in Play Console.
"""
import argparse
import json
import re
import sys

_BRANCH = re.compile(r"^release/v(\d+\.\d+\.\d+)$")
GATES = ("analyze", "test", "aab")
TRACK = "production"
STATUS = "draft"  # never "completed"


def parse_version(branch):
    m = _BRANCH.match(branch or "")
    if not m:
        raise ValueError("not a release snapshot branch: %r" % branch)
    return m.group(1)


def decide(branch, results, last_tag=None):
    """results: dict gate -> conclusion ('success' passes; anything else, or missing, fails)."""
    version = parse_version(branch)
    failed = [g for g in GATES if results.get(g) != "success"]
    ok = not failed
    return {
        "version": version,
        "tag": "mobile-v%s" % version if ok else None,
        "track": TRACK if ok else None,
        "status": STATUS if ok else None,
        "failed_gates": failed,
        "release_note": ("mobile %s drafted for production" % version) if ok else
        ("skipped (not verified), stays on %s" % (last_tag or "the previous mobile-v* tag")),
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--branch", required=True)
    for g in GATES:
        ap.add_argument("--" + g, default="")
    ap.add_argument("--last-tag", default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out", default=None, help="append tag=/track=/status=/release_note= lines (GITHUB_OUTPUT)")
    a = ap.parse_args(argv)
    try:
        d = decide(a.branch, {g: getattr(a, g) for g in GATES}, a.last_tag)
    except ValueError as e:
        print("error: %s" % e, file=sys.stderr)
        return 3
    d["dry_run"] = a.dry_run
    print(json.dumps(d, sort_keys=True))
    if a.out:
        with open(a.out, "a", encoding="utf-8") as f:
            for k in ("version", "tag", "track", "status", "release_note"):
                f.write("%s=%s\n" % (k, d[k] or ""))
            # act = tag + upload really happen; never on a dry run
            f.write("act=%s\n" % ("true" if d["tag"] and not a.dry_run else "false"))
    if d["track"]:
        print("track=%s status=%s tag=%s" % (d["track"], d["status"], d["tag"]))
    return 0 if d["tag"] else 1


if __name__ == "__main__":
    sys.exit(main())
