## 6. Throughput
- S-easy takes up to 4 items per run; S-merge merges every ready PR in one run.
- No concurrency ceiling and no area lock (#1887): throttle #574 (section 10) is the only brake.
- Opus review queue: R-hard (scheduled and dispatched) and S-hard each review up to 4 `needs-opus` items per run, oldest label first.
- Orphan claims: the `orphan-sweep` Action (every 15 min) puts a `status:doing` issue with no open PR (or no new commit on its open PR since the claim) back to `status:todo` after 20 min (`model:sonnet`) or 45 min (`model:opus`). Agents do not run it.
