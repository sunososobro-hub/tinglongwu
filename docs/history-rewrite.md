# Forked before 2026-11-12?

On 2026-11-12 the `dev`/`main` history was rewritten once to normalize commit author info. If you forked earlier:

- **No local changes:** re-fork, or `git fetch upstream && git reset --hard upstream/dev && git push --force origin dev`.
- **Have your own commits:** `git rebase --onto upstream/dev <old-base> <your-branch>` (or cherry-pick them).
- Stuck? Open an issue and we will help.
