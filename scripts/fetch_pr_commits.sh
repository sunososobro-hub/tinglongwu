#!/usr/bin/env bash
# Fetch a PR's commits with retry (#1141): a transient gh API error (rate limit / 5xx)
# must not turn the `test` gate red. Usage: fetch_pr_commits.sh <repo> <pr> <out.json>
# GH_RETRY_SLEEP (seconds, default 20) is the backoff between attempts.
set -u
repo="$1"; pr="$2"; out="$3"
sleep_s="${GH_RETRY_SLEEP:-20}"
for attempt in 1 2 3; do
  if gh api "repos/$repo/pulls/$pr/commits?per_page=100" --paginate --slurp > "$out"; then
    exit 0
  fi
  echo "gh api failed (attempt $attempt/3)" >&2
  [ "$attempt" -lt 3 ] && sleep "$((sleep_s * attempt))"
done
exit 1
