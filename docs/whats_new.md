# What's new

The release notes' **What's new** comes from this file (#589). The bump PR (`release: bump __version__ to X.Y.Z`)
adds a `## vX.Y.Z` section on top with at most 3 plain-language bullets about what users notice (no PR or issue
numbers, no `TODO`). `auto-release.yml` does not merge the release PR until the section for `__version__` exists,
and a release without it stays a draft.

<!-- newest first; example:
## v1.2.3
- Subtitles appear faster on laptops without an NVIDIA card.
-->

## v0.4.11
- While listening, the line you heard now shows up right away in its place, and its translation fills in a moment later.
- The listen window shows which audio source it is using and tells you when the microphone hears only silence.
- Faster PCs: the app now offers a one-click GPU speed-up pack, and Windows shortcuts no longer open an extra Terminal tab.

## v0.4.10
- Live listening now warms up speech recognition before it says "ready", so the first subtitles of a session no longer arrive late.
- Two-way mode on phones is less sensitive: quiet, short background speech no longer turns into chat bubbles.
- Downloading the Whisper speech model now works behind company proxies that block Hugging Face's file listing.

## v0.4.9
- Long talks: subtitles keep up better, with no stretch of more than 10 seconds without a subtitle while speech is still being processed.
- Phone two-way mode: short filler-only sounds (like "um") no longer show up as lines, while short yes-words such as ага, угу, ええ and 어 are kept.

## v0.4.8
- Uninstalling with --purge now removes only tinglongWu's own model files; other apps' files in the same models folder are kept.
- For people working on the source code: opening it with Claude Code no longer writes anything to your computer's settings; if an older version left changes behind, it tells you how to undo them.

## v0.4.7
- Russian meetings: more word forms of common tech terms (past, future, reflexive and plural forms) now get the right translation.
- For people working on the source code: opening it with Claude Code no longer changes your computer-wide git settings, and it undoes what an earlier version changed.

## v0.4.6
- On a fresh install, Start now just listens: it no longer asks you to set up a virtual microphone first.
- Subtitles in the original language show up right after installing, while the translation model finishes downloading in the background.
- Long listening sessions no longer fall further and further behind on a busy computer.

## v0.4.5
- Quiet speech is no longer thrown away before recognition (only true digital silence is skipped), and the words right after a pause are no longer cut off.
- Fewer dropped words on computers where tlwu may only use a few CPU cores: it now plans its work for the cores it is actually allowed to use.
- Installing on a Mac is faster: the translation engine now builds in the background while the models download.

## v0.4.4
- Live subtitles no longer lose words when speech recognition falls behind: the audio keeps being captured, the status shows how many seconds behind it is, and translation waits so recognition can catch up.
- A new subtitle history keeps past sessions on this computer only and deletes them after 30 days.
- Face-to-face mode keeps one speaker label across short, noisy sentences, reads aloud in one voice for the other person's language, and its language sheet shows which language packs are installed.

## v0.4.3
- CPU mode is much faster on laptops without a graphics card: speech recognition and translation no longer fight over the same cores, so a line no longer takes 7–8 seconds while you keep listening.
- Face-to-face mode: each person's speech is now translated into the other person's language automatically.

## v0.4.1
- Translate to now also offers 日本語, 한국어, Español, Français, Deutsch, ไทย, Tiếng Việt and Bahasa Indonesia, and Hindi can be picked as an experimental source language.
- The 情境 (scenario) row is now a single dropdown.

## v0.4.0
- Listen to sound in the room, not only what the computer plays: pick a microphone (Advanced > Microphone (room sound)) to get live subtitles for a lecture or an in-person meeting.
- The settings window keeps its taller size in the one-column layout, so no setting is cut off when you make it narrow.
- In the Windows menu, pressing Enter at the microphone question now just keeps the default instead of printing a usage line.

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
