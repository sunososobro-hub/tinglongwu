## 7. Merge conflicts: resolve in place
- A `dirty` PR: the merger / reviewer merges `origin/dev` into the PR branch (or `update-branch`), resolves conflicts keeping both sides' intent, re-runs the tests and pushes. Close + redo only when the resolution would touch > 50 lines or change the other PR's behaviour.
