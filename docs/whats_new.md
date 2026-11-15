# What's new

The release notes' **What's new** comes from this file (#589). The bump PR (`release: bump __version__ to X.Y.Z`)
adds a `## vX.Y.Z` section on top with at most 3 plain-language bullets about what users notice (no PR or issue
numbers, no `TODO`). `auto-release.yml` does not merge the release PR until the section for `__version__` exists,
and a release without it stays a draft.

<!-- newest first; example:
## v1.2.3
- Subtitles appear faster on laptops without an NVIDIA card.
-->

## v0.3.8
- Subtitles can label who is speaking (a small add-on pack downloads only when you turn it on), and a new 情境 row lets you pick meeting or video.
- The mic now follows your Windows default device, and speak mode holds back more background noise.
- The setup wizard and app speak 繁體中文, 简体中文, 日本語 and Русский, and the settings window can be resized and wraps to one column when narrow.

## v0.3.7
- New chat-style main window: the other side on the left, you on the right, with the original words in small text under each line; one big Start button.
- A mic on/off button (like mute in Meet), and one click shows the subtitles floating over a video.
- Settings now use plain words, with the technical options folded away under Advanced. Speak on Windows is still experimental.

## v0.3.6
- Speak mode no longer sends keyboard or background noise as made-up sentences: unclear lines are held back and shown greyed.
- A "Wrong translation" button on each subtitle line and a "Copy AI prompt" entry in the Help menu make reporting a problem one click.
- Updates are quieter (asked at most once a day, installed at the next launch), and names in the app show as languages, not codes.

## v0.3.5
- The first screen now just asks Listen or Speak and the other side's language; everything else sits under Advanced and a File / Settings / Help menu.
- Speak mode waits for your OK before sending by default, and speak setup can install the virtual audio cable in one click.
- On computers without an NVIDIA card, the app picks lighter settings automatically and marks models that are too heavy; setup progress now shows real download sizes live.

## v0.3.4
- Setup now pre-selects the English and Chinese language packs, and Korean is on the language page, so the first run works without picking packs by hand.
- Language pack downloads resume after an interruption instead of starting over, and each file is checked before use.
- Speak mode no longer offers a made-up subtitle-credit line when the room is silent.
