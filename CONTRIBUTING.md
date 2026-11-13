# Contributing to tinglongWu

**Don't be afraid. Come and contribute. If it fits the project's principles, we will merge it.**
**The default answer is yes: if your PR respects the principles below, we will work with you to get it merged. No contribution is too small, and first-timers are welcome.**

Thanks for stopping by. You do not need to be an expert to help, and we would rather get a rough PR we can work on together than no PR at all.

## What we value

- Small PRs: typo fixes, clearer docs, better error messages.
- Translations of docs and UI strings.
- **Testing on your own hardware.** Mac testers are especially welcome (see the [verification table](docs/details.md#verification-status)), and so are native Russian and Japanese speakers who can tell us when a translation sounds wrong.
- AI-assisted PRs are fine. You are the author: run the code and the tests yourself before opening the PR.

## Before you start

- **Small fix** (typo, docs, a few lines): go straight to a PR.
- **Bigger work** (new feature, refactor, new dependency, anything touching the audio/ASR/translation pipeline): please open an issue or comment on an existing one first, so nobody wastes effort on something that cannot be merged.
- **New here?** Look for the [good first issue](https://github.com/sunososobro-hub/tinglongwu/labels/good%20first%20issue) label. Comment on the issue and say you are taking it.

## Project principles: the only hard rules

These are the only hard rules, so this is exactly the bar. Everything else is negotiable and we will help you with it. If you are unsure whether your PR fits, ask in the issue.

- **Fully offline by default.** Only `tlwu-setup` (install / model download) may use the network. See [docs/privacy.md](docs/privacy.md).
- **No telemetry.** No analytics, no crash reporting, no "phone home".
- **No new cloud dependency in the default path.** A cloud option may exist only if it is opt-in and off by default.
- **Runs on modest hardware:** GPUs with 4-8 GB of VRAM, plus a CPU-only path.
- **English in code, UI strings, comments, and docs** (translated docs live in their own `*.zh-TW.md` / `*.zh-CN.md` files).
- **MIT-compatible dependencies only.** Add new ones to [docs/licenses.md](docs/licenses.md) / [THIRD_PARTY.md](THIRD_PARTY.md) with their license.
- **Never copy code from copyleft projects (GPL / AGPL).** Ideas are fine; code is not. When in doubt, ask.

## Pull requests and credit

- External PRs are welcome.
- The default branch for PRs is `dev` (`main` only receives release PRs).
- PRs are merged by rebase, so your authorship is kept in the commit history.
- Contributors are credited in the release notes.

## Dev setup

Python 3.12 is what CI uses.

```
git clone https://github.com/sunososobro-hub/tinglongwu.git
cd tinglongwu
git checkout dev
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

On Linux you may need `sudo apt-get install -y libsndfile1 libpulse0` (the audio capture libraries are imported by the package). Source install, GPU, and Mac details: [docs/install.md](docs/install.md). Architecture overview: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Running tests

No models and no network are needed.

```
python -m pytest -q
```

CI also checks shell script syntax and runs the whole suite with the network physically disabled (Linux only, needs sudo):

```
for f in scripts/*.sh scripts/i18n/*.sh; do bash -n "$f"; done
bash scripts/ci_offline.sh python -m pytest -q
```

If you cannot run the second one (Windows / Mac), that is fine: CI will, and we will help you read the result.

## Branches and PRs

- **Open PRs against `dev`, not `main`.** `main` is the stable release branch.
- Branch from `dev` and keep one topic per PR.
- Fill in the PR template. It is short.

### Forked before 2026-11-12?

On 2026-11-12 the `dev`/`main` history was rewritten once to normalize commit author info. If you forked earlier:

- **No local changes:** re-fork, or `git fetch upstream && git reset --hard upstream/dev && git push --force origin dev`.
- **Have your own commits:** `git rebase --onto upstream/dev <old-base> <your-branch>` (or cherry-pick them).
- Stuck? Open an issue and we will help.

## What gets merged

- CI is green.
- Focused scope: one thing, done well.
- A clear description of what and why.
- A test, or a short "how I verified" note (for hardware-specific changes, say what OS / GPU / audio device you used).

Every PR gets an AI review plus a maintainer review. The AI review is a first pass to catch the easy things; a human makes the final call.

### Review flow

1. **CodeRabbit** comments first: a quick first pass on style and obvious issues.
2. **Claude review** ([`.github/workflows/claude-review.yml`](.github/workflows/claude-review.yml)) is the merge gate. On PRs into `dev` or `main` (not drafts) it posts one comment covering correctness bugs, packaging/release risks, repo gates, and duplicate or conflicting changes, ending with `VERDICT: MERGE` or `VERDICT: FIX-FIRST`. It reruns on every push.
   - Repo gates: the PR body must contain `Closes #N` or `Refs #N`, and a change to `pyproject.toml` needs the `needs-opus` label.
   - PRs from forks skip this job, because forks never get repository secrets. A maintainer runs the review for them.
3. A maintainer merges after a `VERDICT: MERGE` (or after resolving the FIX-FIRST items).

## Our promise to you

- **First response within about 3 days.**
- If a PR cannot be merged as it is, **we explain why and suggest a path**: a smaller scope, putting it behind a flag, or splitting a follow-up issue. We do not just close it.
- If a PR goes quiet, someone else may pick it up. **Your commits and credit stay** (we keep your authorship).

## Security and conduct

Report security problems privately, see [SECURITY.md](SECURITY.md). Be kind: assume good intent, and keep feedback about the code, not the person.

By contributing you agree your work is released under the [MIT License](LICENSE).
