#!/usr/bin/env python3
"""mobile-apk.yml helper (#1621): decide runner and Play-upload for a run. Prints key=value lines.

    python3 scripts/mobile_upload_plan.py EVENT REF REPO HEAD_REPO HAS_PLAY

Upload to the Play internal track: dev pushes, and PR runs whose head is in this same repo.
Fork PRs never upload and never run on the self-hosted runner. versionCode is the workflow run
number (monotonic across all branches, so a PR build is never lower than an earlier dev build).
"""
import sys


def same_repo_pr(event, repo, head_repo):
    return event == "pull_request" and bool(head_repo) and head_repo == repo


def should_upload(event, ref, repo, head_repo, has_play):
    if not has_play:
        return False
    if event == "push":
        return ref == "refs/heads/dev"
    return same_repo_pr(event, repo, head_repo)


def main(argv):
    if len(argv) != 6:
        print(__doc__, file=sys.stderr)
        return 2
    _, event, ref, repo, head_repo, has_play = argv
    print("upload=%s" % str(should_upload(event, ref, repo, head_repo, has_play == "true")).lower())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
