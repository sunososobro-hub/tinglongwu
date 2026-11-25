# tlwu agent rules

These rules override any routine or task prompt where they conflict. Every tlwu agent reads them from origin/dev at the start of each run. Why each rule exists: `docs/DECISIONS.md`.

Section numbers are shared by three files; "rules section N" means the file that holds N. This file is for everyone. Mergers, R-hard and S-hard (and any patrol wake) also read `.agent/rules_release.md` (sections 11, 14: fast lane, releases) and `.agent/rules_patrol.md` (sections 5, 9, 10, 12-14, 16, 17, 19: supply, triage, ledger, labels, fork PRs, flaky checks, merge-gate Action, board scan). Procedures: `.agent/routines/common.md` + the role's file.

## Sections (#1917)
Each section's text lives in its own file under `.agent/rules/`, so unrelated rule changes stop editing one shared file. This file plus every file listed below, in this order, is the whole rule set. Change a rule in its section file; a new section is a new `.agent/rules/NN_topic.md` plus one line here.
- Claude Code loads the files through the `@` imports below (`CLAUDE.md` imports this file).
- In a clone: `python scripts/rules_text.py` prints the whole text.
- Over REST, one shell call: `for f in $(gh api "repos/$R/contents/.agent/rules?ref=dev" --jq '.[].name'); do gh api "repos/$R/contents/.agent/rules/$f?ref=dev" -H "Accept: application/vnd.github.raw"; done`

- @rules/01_who.md — 1. Who does what
- @rules/02_locking.md — 2. Locking
- @rules/03_reviews.md — 3. Reviews and hard stops
- @rules/04_model.md — 4. Model choice
- @rules/06_throughput.md — 6. Throughput
- @rules/07_conflicts.md — 7. Merge conflicts: resolve in place
- @rules/08_overlap.md — 8. Overlapping files: the author merges dev
- @rules/10_throttle.md — 10. Throttle, picker, requeue, usage line
- @rules/13_infra.md — 13. Infra cap
- @rules/14_merge_safety.md — 14. Merge safety: shared definitions
- @rules/15_issue_format.md — 15. Issue format
- @rules/17_dev_gate.md — 17. dev merge gate
- @rules/18_contributor_machine.md — 18. Repo code never changes a contributor's machine
- @rules/20_timing.md — 20. Timing checks
