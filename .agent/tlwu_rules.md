# tlwu agent rules (override)

This file overrides any routine prompt wherever they conflict (what to pick, whether to work, numbers).

Every tlwu agent (cloud routines W-easy, W-hard, R-merge, R-hard, S-easy, S-merge, S-hard, and the home-PC pipeline) reads this file from origin/dev at the start of each run. When it conflicts with the agent's own prompt, THIS FILE WINS. Owner-approved 2026-11-13.

## 1. Who does what
- Home PC pipeline: only issues labelled `os:windows`, `需實測` or `local-only` (needs Windows, GPU, real audio devices). Max 3 at once.
- Cloud workers (W-easy, W-hard, S-easy, S-hard): everything else. **Skip** any issue labelled `os:windows`, `需實測` or `local-only`.
- Cloud mergers (R-merge, S-merge, R-hard): merge every ready PR, including home-PC PRs. Home-PC workers stop after opening the PR (no CI polling, no self-review, no merge).

## 2. Locking (one rule for everyone)
- Claim = create branch `fix/N` from dev (REST `POST git/refs`, or `create_branch`). "Reference already exists" (422) = someone else has it: take the next item.
- After claiming: add `status:doing`. Push work to `fix/N` (re-do after a closed PR: `fix/N-r2`, `-r3`).
- Never start an item that has an open PR whose body says Closes/Refs #N, or a `fix/N*` branch.

## 3. Merge gates (changes to g5 / g7, plus hard stops)
- g5 size: escalate to Opus (`needs-opus`) only when the diff is > 600 changed lines (was 300). Sensitive paths (g4) unchanged.
- g7 CI: wait up to **10 minutes** for checks to complete (was 4) before giving up; don't post "CI still running" comments.
- `.github/` changes: NOT an owner question. Merge to dev after CI green + Opus review (`Opus review: PASS`). Never release, tag, or touch main.
- Hard stops, never merge when:
  - any review/comment on the PR says `needs owner` / `blocking` and has not been resolved by a later Opus PASS;
  - the linked issue has `status:frozen`, `status:proposal` or `needs-owner`.

## 4. Model choice
- Use Opus only for: concurrency/threading, audio routing/devices, translation quality (MT/ASR evals). Everything else Sonnet, including "> 150 lines" and "cross-module" work.
- If a Sonnet attempt fails once (PR closed or CI red twice), relabel `model:opus` and retry as `fix/N-r2`.

## 5. Keep the queue full
- When open `status:todo` + `ok-to-work` issues < 10: an Opus run (S-hard) splits `status:blocked` issues that already carry a split proposal into child issues, and may open new **bug / test / eval** issues only (no new features). Each new issue needs Why / Goal / one measurable Acceptance line / Verify.
- Work-mix budget (owner decision 2026-11-10): fix 30% / new features 30% / QA 15% / eval 10% / ideas 5% / docs 5% / maintenance 5% of worker minutes. QA (15%) and ideas (5%) run on fixed schedules (S-hard spot checks; ideation below). Ideation: when open `status:proposal` < 3, S-hard may open up to 2 `[idea]` issues per day as `src:ai enhancement status:proposal` (never status:todo; the owner promotes them). S-hard's daily 00:28 ratio check (sum `tlwu-usage` minutes of PRs merged to dev in the last 7 days by type label vs these targets) stays. Ideation minutes have no merged PR: S-hard records them in a `tlwu-usage` comment on each `[idea]` issue, and the ratio check counts those comments (created in the same 7 days) in the ideas bucket. QA spot checks likewise have no merged PR: S-hard records their minutes in a `tlwu-usage` comment on the `[health] pipeline check log` issue, and the ratio check counts those comments in the QA bucket.
- Mix enforcement: pickers (S-easy, W-*, local worker) honour the mix. S-hard's 00:28 run writes the 7-day shares into the `[health] pipeline check log` issue; when a type is above 1.3x its target, pickers skip that type at equal priority (P0/P1 always allowed) and take an under-target type first; when eval is under half its target, S-hard opens up to 2 eval issues (status:todo) per day.

## 6. Throughput
- S-easy: take up to 4 items per run. S-merge: merge every ready PR in one run.
- Concurrency ceiling across all agents: `status:doing` ≤ 10.

## 7. Merge conflicts: resolve in place (added for #566)
- When a PR is `dirty` (mergeable_state), the merger/reviewer first merges `origin/dev` into the PR branch (or `update_pull_request_branch`), resolves conflicts keeping both sides' intent, re-runs the tests, and pushes. Do not close + redo first.
- Close + redo only if the resolution would touch > 50 lines or change the behaviour of the other PR.
- The local `~/.claude/tlwu_pipeline/prompt.md` needs the same rule (out of repo scope; owner session to apply).

## 8. Picker: avoid overlapping files (added for #566)
- Before claiming, list files changed by every open PR (`gh api repos/<R>/pulls/N/files`). Skip an issue whose `area:*` or named files overlap an open PR's files, except P0/P1.

## 9. Health log
- The S-hard health log line adds `conflicts_resolved=<n> conflicts_closed=<n>`.

## 10. Ledger, picker, throttle (owner 2026-11-13)
This section supersedes the "Mix enforcement" bullet in section 5 and the 7-day ratio check wherever they conflict (the picker below replaces the 1.3x skip rule).

### Throttle (faucet)
- Every routine (and the home-PC worker) first reads the first line of issue #574 `[pipeline] throttle (faucet)`: `gh api repos/sunososobro-hub/tinglongwu/issues/574 --jq .body` -> first line `level: N`. Unreadable or missing -> assume `level: 2`. Obey the legend:
  - 0 off: only merge green PRs; no picking, no new issues.
  - 1 low: only P0/P1 items + merging; only worker slot A works (B, C, W-hard idle unless P0/P1).
  - 2 normal: ledger + picker below, pace brake on: if this week's ledger minutes are ahead of the hour-of-week pace (week minutes so far / expected minutes at this hour of the week) by more than 10%, slots B and C skip this wake.
  - 3 high: all slots work, up to 4 items per wake, no pace brake.
  - 4 max (reset-credit week): as 3, plus the home-PC worker runs 3 at once and S-hard also works items after its sweep.
- Mergers (R-merge, S-merge, R-hard) merge green PRs at every level.

### Slots
- Hourly Sonnet workers: slot A = S-easy (:13), slot B (:33), slot C (:53). Opus worker = W-hard (hourly :48 + dispatch).

### Triage is not work
- On `issues.opened`, W-easy only triages: adds `type` (bug / enhancement / eval / documentation / maint), `P0..P3`, `model:*` and one `area:*` label. It works the item immediately only if it is P0, or owner-authored and explicitly marked `urgent`. Everything else waits for the slot workers.

### Ledger (S-hard, hourly)
- Sum the `tlwu-usage: ... minutes=N` last line of PRs merged to dev in the last 48 h, grouped by the linked issue's type label. Post a new comment on the pinned `[health] pipeline check log` issue (#61; create and pin it if missing) with a table: `type | minutes | share | target | deficit` where deficit = target - share (percentage points).
- Targets: bug 30, enhancement 30, eval 10, documentation 5, maint 5. QA 15 and ideas 5 run on fixed schedules (section 5) and are also logged in the table.

### Picker (every worker)
- P0/P1 always first.
- Otherwise read the latest ledger comment on #61, pick the type with the largest deficit; inside a type order P0 -> P3, then lowest issue number. If that type has no eligible item, take the next-largest deficit. Never idle while any eligible item exists (subject to the throttle level).
- No ledger comment in the last 3 h -> pick by priority then lowest number.

### Supply (S-hard)
- When a type has < 5 eligible todo items, open new ones only with evidence: failing test or QA spot check, crash log, user report, eval gap, or a merged change lacking docs. Features only from owner-approved proposals (`status:proposal` promoted by the owner). No evidence -> do not open.

### Area lock
- Each issue carries exactly one `area:*` label. A worker skips an issue whose area already has an open PR or a `status:doing` issue (P0 exempt). This tightens section 8 (file overlap) - both apply.

### Usage line
- Every PR body must keep `tlwu-usage: ... minutes=N` as its last line; it is the ledger input. PRs without it count as 0 minutes.
