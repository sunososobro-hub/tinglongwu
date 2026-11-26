#!/usr/bin/env python3
"""mobile-apk.yml helper (#1621): decide runner and Play-upload for a run. Prints key=value lines.

    python3 scripts/mobile_upload_plan.py EVENT REF REPO HEAD_REPO HAS_PLAY

Upload to the Play internal track only on a push to dev (#1969). PR runs (same-repo or fork) build
but never upload: the owner tests after merge, a per-PR upload cost 10+ minutes, and a rerun of an
old PR run was rejected by Play (versionCode = workflow run number, #1934). A fallback run keeps the
original event, so it follows the same rule. Fork PRs never run on the self-hosted runner.
"""
import sys


def should_upload(event, ref, repo, head_repo, has_play):
    if not has_play:
        return False
    return event == "push" and ref == "refs/heads/dev"


def main(argv):
    if len(argv) != 6:
        print(__doc__, file=sys.stderr)
        return 2
    _, event, ref, repo, head_repo, has_play = argv
    print("upload=%s" % str(should_upload(event, ref, repo, head_repo, has_play == "true")).lower())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
