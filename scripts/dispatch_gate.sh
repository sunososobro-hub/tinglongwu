#!/usr/bin/env bash
# #2012: deterministic dispatch gate. A script decides which worker triggers to fire; the LLM only fires them.
# Prints `FIRE <trigger_id>` per trigger to fire and `ALERT <pr>` per PR rejected 3+ times; nothing when nothing is due.
# gh api REST only. Env: R (repo), GH (gh binary), TRIGGERS (json file), REVIEW_AGE_MIN (default 60).
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
R="${R:-sunososobro-hub/tinglongwu}"
GH="${GH:-gh}"
TRIGGERS="${TRIGGERS:-$HERE/dispatch_triggers.json}"
REVIEW_AGE_MIN="${REVIEW_AGE_MIN:-60}"

ids() { python3 -I -c 'import json,sys; v=json.load(open(sys.argv[1])).get(sys.argv[2], []); print(*([v] if isinstance(v, str) else v), sep="\n")' "$TRIGGERS" "$1"; }

level=$("$GH" api "repos/$R/issues/574" --jq .body 2>/dev/null | head -1 | sed -n 's/^level: *\([0-9]\).*/\1/p')
level="${level:-2}"
[ "$level" = 0 ] && exit 0

EXCL='-label:needs-triage -label:needs-opus -label:needs-owner -label:status:blocked -label:status:doing -label:status:review -label:status:proposal -label:status:frozen'
count() { "$GH" api -X GET search/issues -f q="repo:$R is:issue is:open label:status:todo label:ok-to-work $EXCL $1" -f per_page=1 --jq .total_count 2>/dev/null || echo 0; }

fire_n() { # fire_n <ready count> <json key>: first min(ready, listed) ids
  local n="$1" id
  for id in $(ids "$2"); do
    [ "$n" -gt 0 ] 2>/dev/null || break
    echo "FIRE $id"; n=$((n - 1))
  done
}

sonnet=$(count '-label:model:opus'); opus=$(count 'label:model:opus')
[ "$level" = 1 ] && sonnet=$(( sonnet > 0 ? 1 : 0 ))
fire_n "$sonnet" sonnet_workers
fire_n "$opus" opus_workers

now=$(date -u +%s); fire_review=0
for n in $("$GH" api "repos/$R/pulls?state=open&base=dev&per_page=100" --jq '.[]|select(.draft|not)|.number' 2>/dev/null); do
  read -r state sha < <("$GH" api "repos/$R/pulls/$n" --jq '"\(.mergeable_state) \(.head.sha)"' 2>/dev/null)
  fails=$("$GH" api "repos/$R/issues/$n/comments?per_page=100" --jq '[.[]|select(.body|test("^(AI review: FAIL|Opus review: CHANGES)"))]|length' 2>/dev/null || echo 0)
  [ "${fails:-0}" -ge 3 ] && echo "ALERT $n"
  [ "$state" = clean ] || continue
  green=$("$GH" api "repos/$R/commits/$sha/check-runs?per_page=100" --jq '[.check_runs[]|select(.name=="test" and .conclusion=="success")|.completed_at|fromdateiso8601]|max // empty' 2>/dev/null)
  [ -n "$green" ] && [ $(( (now - green) / 60 )) -gt "$REVIEW_AGE_MIN" ] && fire_review=1
done
[ "$fire_review" = 1 ] && for id in $(ids reviewer); do echo "FIRE $id"; done
exit 0
