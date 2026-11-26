## 10. Throttle, picker, requeue, usage line
### Throttle
- Every routine first reads line 1 of issue #574: `level: N` (unreadable or missing -> 2).
  - 0 off: only merge green PRs; no picking, no new issues.
  - 1 low: only P0/P1 items + merging; only slot A works (B, C, W-hard only for P0/P1).
  - 2 normal: picker below; slots B and C skip the wake when this week's tool calls > 1.10 x W x hours elapsed / 168 (week from Monday 00:00 Asia/Taipei; calls as in the ledger; W = the previous full week in calls, none -> no brake).
  - 3 high: all slots work, up to 4 items per wake, no pace brake.
  - 4 max: as 3, plus S-hard also works items after its sweep.
- Mergers merge green PRs at every level.
- Slots: Sonnet workers slot A = S-easy (:13), slot B (:33), slot C (:53); Opus worker W-hard (:48 + dispatch).

### Picker (every worker)
- Eligible: `status:todo` + `ok-to-work`, no `needs-triage`, plus the role file's filters; over the infra cap (section 13) skip `infra` except P0 and `src:owner`.
- Priority: `area:ci` / release issues are P1 only while the dev `test` check is red or a release PR has been stuck > 6 h, otherwise P2. Bugs from an owner field test (real meeting or device use) are P1.
- P0/P1 first: no ledger type. Order: lower P, focus match, lowest number.
- Otherwise read the newest ledger comment on #61 (`rules_patrol.md` section 10) and take the type with the largest deficit; inside it focus matches first, then P0 -> P3, then lowest number. No eligible item -> next-largest deficit, then any type by P and number. Never idle while an eligible item exists. No ledger comment in the last 3 h -> by P, then lowest number.
- Focus: line 2 of #574 `focus: <values>` (mode / platform / language, e.g. `speak, ja`); missing = none.
- `src:owner` issues are never held back by the type mix.
- At level >= 3 each Sonnet slot keeps claiming until it has opened 4 PRs this wake or nothing is eligible; W-hard and S-hard take up to 2.
- A wake that ends with < 4 items while eligible items existed lists `skipped: #N(reason)` for up to 5 P1/P2 items in its final line.

### Requeue
- No area lock (#1887): an `area:*` label never stops a claim, whatever number of `status:doing` issues share it.
- Requeue: `board-scan` puts the issue of a PR red, `dirty` or `needs-review-ai` for > 30 min back to `status:todo` (comment `requeue: ...`); a worker fixes it on the same branch (section 2). After 3 requeues: `needs-opus`.

### Usage line
- Every PR body ends with `tlwu-usage: model=<m> tool_calls=<n> minutes=N`: `tool_calls` is the ledger input (missing = falls back to `minutes`); `minutes` (claim to PR open, wall time) is no longer a cost measure, and the ledger drops any value over 600 as bad data.
- Cap per ticket per run: 50 tool calls (Sonnet and Opus). At 45 the worker stops exploring and wraps up. At 50 (or about 40 min, whichever first) it pushes what exists if tests are green and opens the PR; otherwise it drops unfinished edits, comments one line with what is left, adds `needs-split` + `status:todo` and stops. A fix round on an open PR has its own cap of 20 calls. Hand-off: `routines/worker.md` "Hand-off".
- A run that pushes a fix to an open PR (CodeRabbit, red `test`, conflict, FAIL / CHANGES, requeue) ends with a PR comment whose last line is `tlwu-usage: model=<m> tool_calls=<n> minutes=N phase=fix`; the ledger adds the calls to the PR's type.
