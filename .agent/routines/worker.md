# Worker routines: W-hard (Opus) and S-easy slots A / B (Sonnet)

Read after `common.md`. "Opus worker" = W-hard; "Sonnet worker" = S-easy.

## Pick
- Sonnet worker: first read the throttle level (issue #574, first line `level: N`) and the newest ledger entry, and decide per rules §10 (slots, picker) whether to work and which type to take.
- Candidates: open issues (anything with a `pull_request` field is skipped) with `status:todo` + `ok-to-work` and none of `needs-owner` / `needs-opus` / `needs-triage` / `status:blocked` / `status:doing` / `status:review` / `status:proposal` / `status:frozen`. Re-read labels and author with `gh api repos/$R/issues/N` before deciding.
  - Opus worker: must have `model:opus` and be opened by the owner. Sonnet worker: must NOT have `model:opus`.
  - `os:windows`, `local-only`, `需實測` are taken as normal (rules §1: Windows code is verified by GitHub Windows CI; 需實測 = finish and open the PR with `Refs #N`, the owner tests after merge).
- No open PR may point at it (head `fix/N*` or body `Closes/Refs #N`), except the rules §2 FAIL / CHANGES case and a `requeue:` comment (rules §10): then claim the fix round first (after `git fetch origin pull/<PR>/head`): `bash scripts/claim_branch.sh claim/N-<PR head sha7> <PR head sha> <N> <session>` (exit 1 = another worker has this round: next item; `stale-branches` deletes `claim/*` after 45 min), check out that PR's branch (not the claim branch), fix what the newest FAIL / CHANGES / requeue comment names (red `test`, merge `origin/dev` for `dirty`, answer and resolve open review threads), push once, and swap `status:doing` -> `status:review`. A `requeue: partial PR … merged` item has no open PR: check each `Acceptance:` line on dev first; all met -> comment the evidence, label `status:done`, close the issue; otherwise do the rest on the next free `fix/N-rK`.
- Order: Sonnet worker takes the most-behind type per the ledger; within a type P smallest, then number smallest; P0 / P1 always first. Opus worker: P smallest, then number smallest.
- How many: Sonnet worker up to 4, one after another. Opus worker 1 (2 when the rules §10 level >= 3).
- Nothing to take -> Report.

## Lock and claim
- First clone / fetch per common.md and set the git identity (rules §2): the claim script needs the base commit locally. `git fetch origin dev && D=$(git rev-parse origin/dev)` (a retry from a closed PR: `git fetch origin pull/<PR>/head` and use that head). Branch `B=fix/N`; if it already exists (leftover of a closed PR: do not delete it) use `fix/N-r2`, then `-r3`, up to `-r5`. Lock with `scripts/claim_branch.sh $B $D N <session>` per common.md "Writes" (base = dev tip `$D`, or the closed PR's head for a retry, rules §2). Locked by someone else -> next item (Opus worker tries at most 3).
- Claim: remove `status:todo`, add `status:doing`, comment `doing` (Opus worker: `doing (opus)`).

## Work
1. `git fetch origin $B`, `git checkout -B $B origin/$B`. Install test deps.
2. Read the issue and ALL comments (`gh api "repos/$R/issues/N/comments?per_page=100"`). Later owner (sunososobro-hub) comments override the body; a comment that says not to do something wins. If an earlier PR was closed unmerged, read why first.
3. No measurable `Acceptance:` -> comment one line adding one, then follow it.
4. Find the root cause statically (read code, don't guess), fix it, and add a test that fails before and passes after. Tests exercise behaviour (import and call, or run the shell / CLI); reading or grepping source is never a test. A changed threshold / constant / parameter needs its data source in the PR body; no data -> don't change the value. Run the related tests first. Just before the final run, `git fetch origin dev && git merge --no-edit origin/dev` and resolve any conflict yourself, keeping both sides' intent (rules §8; a fix round on an open PR does the same before its push); then one `python -m pytest -q`.
5. Hard stops (only these): needs a change to `.claude/`, `LICENSE`, `SECURITY.md`, permissions or secrets; needs deleting or skipping tests; more than ~300 changed lines (comment a split proposal); owner comments that conflict with no way to satisfy both. Sonnet worker also stops on any `.github/` change. -> add `needs-opus` + `status:blocked`, one-line comment with the reason, no PR.
   - Opus worker does `.github/` changes (S-split routes them to `model:opus` for this reason) and opens the PR; release-critical workflows then get the Opus review (rules §3 / §14). Netguard / offline code, install / update scripts, dependencies, models, and issues saying "owner review at merge": do the work and open the PR; review sorts it out.
6. `git push -u origin $B`. Open the PR: base `dev`, title `fix #N: …`, body = 1-3 lines + `Acceptance:` with each item -> measured result (real command output / numbers) + `Owner comments:` with each owner point -> how it was handled (`none` if none) + `Closes #N` (需實測 -> `Refs #N`, and comment `how to test: …` on the issue) + last line `tlwu-usage: model=<opus|sonnet> tool_calls=<n> minutes=<lock-to-PR minutes>` (`tool_calls` is what the ledger counts; count your calls honestly). Issue `status:doing` -> `status:review`.
7. CodeRabbit once (rules §3 g7 exception): `sleep 270` once, then read once `gh api "repos/$R/pulls/<PR>/reviews?per_page=100"` and `.../pulls/<PR>/comments?per_page=100` (author `coderabbitai[bot]`). Reviewed -> rules §3 "One review round" in this run: Critical / Major fixed with tests in one push; Minor / nitpick get a one-line reply and are resolved, no push; if you pushed, add a PR comment whose last line is `tlwu-usage: model=<m> tool_calls=<n> minutes=<n> phase=fix`. Not reviewed -> this item is done. Never a second wait; never wait for CI.

## Call cap and hand-off (#1915)
- Hard cap 50 tool calls per ticket per run (Sonnet and Opus); a fix round on an open PR 20. Count every tool call. At 45 stop exploring and wrap up. At 50, or about 40 min, whichever first: tests green -> push and open the PR; otherwise drop unfinished edits (only tested, pushed work stays), comment one line with what is left, add `needs-split` + `status:todo`, and stop.
- Before stopping with work left, post or update ONE PR comment starting `handoff:` with: done, next step, decisions and why, dead ends already tried, files the next run must read first. State lives on the PR branch + that comment, never in chat.
- The next worker (same PR, or `fix/N-rK`) reads the `handoff:` comment first and continues; it does not re-research the dead ends.
- At most 2 hand-offs per issue; a step that fails twice -> `needs-split` + `status:blocked`, no third run. Do not hand off a segment under about 15 min of work (a cold start costs about 60k tokens).

## Side findings (max 2 per run; search for duplicates first)
- English title prefixed `[bug]` / `[feat]` / `[docs]` / `[test]` / `[idea]` + one `area:*`. First line `Found by AI worker while fixing #N.` Body has `Goal:` (one sentence), `Acceptance:` (measurable: number / behaviour / command output), `Out of scope:`, `Verify:`.
- Labels: `src:ai`, `ok-to-work`, `P2` / `P3`, `model:sonnet` (`model:opus` only for concurrency / audio routing / translation quality), type. A bug with evidence -> `status:todo`; an idea -> `status:proposal`; not testable on device -> `需實測`. No padding.

## Report (post to #61 per common.md "Reporting")
- Sonnet worker: `✅ 收工 檔位=<N> PR #N→#M …`; nothing -> `✅ 收工 無單`; takeable but skipped -> `skipped: #N(reason)`.
- Opus worker: `✅ 收工 #N 略過(reason) | 硬停 #N(reason) | PR #N→#M`.
