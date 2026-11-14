# What's new

The release notes' **What's new** comes from this file (#589). The bump PR (`release: bump __version__ to X.Y.Z`)
adds a `## vX.Y.Z` section on top with at most 3 plain-language bullets about what users notice (no PR or issue
numbers, no `TODO`). `auto-release.yml` does not merge the release PR until the section for `__version__` exists,
and a release without it stays a draft.

<!-- newest first; example:
## v0.3.4
- Subtitles appear faster on laptops without an NVIDIA card.
-->
