# tlwu agent rules (override)

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

## 6. Throughput
- S-easy: take up to 4 items per run. S-merge: merge every ready PR in one run.
- Concurrency ceiling across all agents: `status:doing` ≤ 10.
