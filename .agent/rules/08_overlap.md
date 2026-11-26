## 8. Overlapping files: the author merges dev
- No look at other PRs before claiming (#1887, C6 of #1875): workers do not list open PRs' files and do not skip an issue because another open PR touches the same files or area.
- Instead the author, just before its final test run, runs `git fetch origin dev && git merge --no-edit origin/dev` and resolves any conflict itself while it still holds the context, keeping both sides' intent (`routines/worker.md` Work step 4). A PR that goes `dirty` after that follows section 7.
