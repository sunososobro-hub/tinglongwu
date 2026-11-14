# What's new

The release notes' **What's new** comes from this file (#589). The bump PR (`release: bump __version__ to X.Y.Z`)
adds a `## vX.Y.Z` section on top with at most 3 plain-language bullets about what users notice (no PR or issue
numbers, no `TODO`). `auto-release.yml` does not merge the release PR until the section for `__version__` exists,
and a release without it stays a draft.

<!-- newest first; example:
## v1.2.3
- Subtitles appear faster on laptops without an NVIDIA card.
-->

## v0.3.4
- Setup now pre-selects the English and Chinese language packs, and Korean is on the language page, so the first run works without picking packs by hand.
- Language pack downloads resume after an interruption instead of starting over, and each file is checked before use.
- Speak mode no longer offers a made-up subtitle-credit line when the room is silent.
