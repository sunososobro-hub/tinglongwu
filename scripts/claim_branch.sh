#!/usr/bin/env bash
# #1882 (C1 of #1875): atomic claim of a work branch for cloud workers.
#
#   scripts/claim_branch.sh <branch> <base-sha> <issue-number> [session]
#
# Pushes a new EMPTY commit (tree and parent = <base-sha>, message `claim #N <session> <nonce>`) to
# refs/heads/<branch>, without force and with an empty lease (`--force-with-lease=<ref>:` = the branch must not
# exist). The server updates the ref atomically, so of two workers claiming the same branch exactly one wins:
# - the commit is unique (random nonce), so a second push is never "Everything up-to-date" (the #1865 double
#   claim: both pushed the same dev sha and both exited 0);
# - the empty lease rejects the push when the branch already exists, even when the new commit would
#   fast-forward it (an old claim branch sitting on a dev commit).
# Exit 0 = claimed (prints the claim sha); exit 1 = taken by someone else (or the push failed): take the next item.
#
# Branches: `fix/N` (or `fix/N-rK`) on the dev tip for a new item; `claim/N-<head sha7>` on the PR head for a fix
# round on an open PR (the claim commit stays on that claim branch only; the fix is pushed to the PR branch).
set -euo pipefail

if [ "$#" -lt 3 ]; then
  echo "usage: $0 <branch> <base-sha> <issue-number> [session]" >&2
  exit 2
fi
branch=$1
base=$2
issue=$3
session=${4:-${CLAUDE_SESSION_ID:-local}}
nonce=$(od -An -N8 -tx8 /dev/urandom | tr -d ' \n')

tree=$(git rev-parse "${base}^{tree}")
commit=$(git commit-tree "$tree" -p "$base" -m "claim #${issue} ${session} ${nonce}")
if git push --porcelain --force-with-lease="refs/heads/${branch}:" origin "${commit}:refs/heads/${branch}" >/dev/null 2>&1; then
  echo "$commit"
  exit 0
fi
echo "taken: ${branch}" >&2
exit 1
