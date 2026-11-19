#!/usr/bin/env python3
"""Mobile release-lane promotion plan (#1534), dry-run only.

    python3 -I tool/mobile_release_plan.py <runs.json> [--auto true|false] [--pubspec P] [--whats-new W] [--head-sha SHA]

runs.json = the JSON of `GET /repos/<R>/actions/workflows/mobile-apk.yml/runs?branch=dev`.
Prints the plan as JSON (build run, sha, versionName, notes, blockers). Exit 0 when every gate
passes (would promote), 1 when a gate blocks (a plan was printed), 2 on bad usage,
3 on an execution error (missing file, bad JSON). --head-sha = the checked-out commit; the plan blocks
when it differs from the selected run's head_sha (release files must come from the built commit). It never talks to Play.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # -I drops the script dir
import mobile_version as mv  # noqa: E402


def newest_green_build(runs):
    """Newest completed, successful push run of mobile-apk on dev (the AAB was built and tests ran)."""
    ok = [r for r in runs
          if r.get("status") == "completed" and r.get("conclusion") == "success"
          and r.get("event") == "push"]
    if not ok:
        return None
    return max(ok, key=lambda r: r.get("run_number", 0))


def build_plan(runs, pubspec_text, whats_new_text, auto_enabled, head_sha=None):
    """Return a dict with `blockers` (empty = would promote)."""
    blockers = []
    if not auto_enabled:
        blockers.append("MOBILE_AUTO_RELEASE is not 'true' (kill switch)")
    run = newest_green_build(runs or [])
    if run is None:
        blockers.append("no green mobile-apk push run on dev")
    plan = {"build_run": None, "sha": None, "versionName": None, "aabBuildName": None, "notes": None}
    if run is not None:
        plan["build_run"] = run.get("run_number")
        plan["sha"] = run.get("head_sha")
        if head_sha is not None and head_sha != plan["sha"]:
            blockers.append("checked-out commit %s is not the selected build's commit %s"
                            % (head_sha[:12], str(plan["sha"])[:12]))
    try:
        plan["versionName"] = mv.version_name(pubspec_text)
        plan["notes"] = mv.notes_section(whats_new_text, plan["versionName"])
        if plan["build_run"] is not None:  # the AAB mobile-apk built carries this name (-dev.<run>)
            plan["aabBuildName"] = mv.dev_name(plan["versionName"], plan["build_run"])
    except ValueError as e:
        blockers.append(str(e))
    plan["blockers"] = blockers
    return plan


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def main(argv):
    args = argv[1:]
    if not args:
        print(__doc__, file=sys.stderr)
        return 2
    runs_path = args[0]
    auto, pubspec, notes, head = False, mv.PUBSPEC, mv.WHATS_NEW, None
    it = iter(args[1:])
    try:
        for a in it:
            if a == "--auto":
                auto = next(it).lower() == "true"
            elif a == "--pubspec":
                pubspec = next(it)
            elif a == "--whats-new":
                notes = next(it)
            elif a == "--head-sha":
                head = next(it)
            else:
                raise StopIteration
        data = json.loads(_read(runs_path))
        runs = data.get("workflow_runs", []) if isinstance(data, dict) else data
        plan = build_plan(runs, _read(pubspec), _read(notes), auto, head)
    except StopIteration:
        print(__doc__, file=sys.stderr)
        return 2
    except (OSError, ValueError) as e:
        print("error: %s" % e, file=sys.stderr)
        return 3
    print(json.dumps(plan, indent=2, ensure_ascii=False))
    return 1 if plan["blockers"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
