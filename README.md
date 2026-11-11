# tinglongWu — Taiwanese 聽攏有 (thiann lóng ū): "I understood it all". Real-time speech translation, offline at runtime and verifiable (install and model download need the network; the in-process guard is defence in depth, not proof, see Privacy model).

[English](README.md) | [繁體中文](README.zh-TW.md) | [简体中文](README.zh-CN.md)

Architecture / 架構 / 架构: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) · [zh-TW](docs/ARCHITECTURE.zh-TW.md) · [zh-CN](docs/ARCHITECTURE.zh-CN.md)

Fully local, offline, real-time speech translator: **system audio output -> capture -> ASR -> translate -> text**.
It captures whatever the PC plays (the system output mix, not per-app). There is no network use at runtime; only setup / first-run model download uses the network.
Nothing leaves your machine: no cloud APIs, no telemetry, no audio or transcripts written to disk.

```
[RU] Привет, как дела?
[EN] Hello, how are you?
[ZH] 你好，你過得怎麼樣？
```

> **Before you use it in a meeting:** tell the other participants you are running live translation (it captures the call audio and transcribes it on your PC; nothing is uploaded). See [Acceptable Use / Consent](#acceptable-use--consent).

<!-- TODO: 30-second demo GIF goes here (docs/demo.gif) -->

## One-command install (Windows / Mac / Linux)

Each command installs the exact version of the latest GitHub Release (`main` is stable; `dev` is for development). Re-running is safe.

| OS | Command |
|---|---|
| Windows (PowerShell) | `irm https://github.com/sunososobro-hub/tinglongwu/releases/latest/download/install.ps1 \| iex` |
| macOS (Terminal) | `curl -fsSL https://github.com/sunososobro-hub/tinglongwu/releases/latest/download/mac_install.sh \| bash` |
| Linux (bash) | `curl -fsSL https://github.com/sunososobro-hub/tinglongwu/releases/latest/download/install.sh \| bash` |

Each release also lists `SHA256SUMS.txt`; to check a script before running it, download it and compare. The Linux installer asks y/N before any `apt` step and downloads no models (the menu asks before every download). The Windows command downloads the source to `%USERPROFILE%\tinglongwu` and runs the same `install.ps1` as below (the execution-policy bypass is for that one child process only).

## Quick install (Windows)

For Windows 10/11 with an NVIDIA GPU (works without one, just slower). You need [Python 3.12](https://www.python.org/downloads/windows/) first (tick **Add python.exe to PATH** in its installer).

1. On GitHub click **Code > Download ZIP**, then right-click the ZIP > **Extract All**.
2. Open the folder and double-click **`install.bat`**. Wait (10-30 min, up to ~3 GB of models on an NVIDIA GPU with 4 GB+ VRAM, ~1 GB otherwise; safe to re-run if interrupted).
3. Double-click the new desktop shortcut **聽攏有 tinglongWu**.

**Using the menu.** The shortcut opens a menu: `1` Meeting (listen + speak into Meet/Zoom, wear a headset; set the meeting microphone to *CABLE Output*), `2` Speak only (Chinese -> English/Russian/Japanese voice), `3` Listen only (foreign speech -> Chinese subtitles), `L` change the other side's language, `G` graphical interface, `U` update now, `A` toggle auto-update at start, `I` menu language. Press the key and Enter; every model download asks y/N first.

To remove: delete the folder and the shortcut. `install.ps1 -DryRun` only shows what would be done. The Qwen engine uses `requirements-qwen.txt` (CUDA build of llama-cpp-python); if no matching wheel exists the installer falls back to the OPUS engine automatically.

## Mac one-line install

Apple Silicon and Intel, macOS 12+. In Terminal:

```
curl -fsSL https://github.com/sunososobro-hub/tinglongwu/releases/latest/download/mac_install.sh | bash
```

It checks Homebrew (never installs it silently; prints the official command if missing), installs Python 3.12, git, BlackHole 2ch and 16ch, clones to `~/tinglongwu`, creates the venv, sets up the English voice (Kokoro, own venv in `~/tts-kokoro`, model in `~/models/kokoro`), downloads the models, and installs `tlwu-menu`. Re-running is safe (repair/update). The one manual step it prints at the end: in Audio MIDI Setup create a Multi-Output Device (speakers/headphones + BlackHole 2ch) and set it as the system output; set the Meet/Zoom microphone to **BlackHole 16ch**. Audio design: BlackHole 2ch = capture of system audio (listening), BlackHole 16ch = virtual microphone for the synthesized speech, so your own TTS never loops into listening. Run `tlwu-menu` afterwards; on the first menu run it asks once whether to check GitHub for updates at every menu start (`git pull --ff-only`, about 6 s cap, offline-safe); if you say no, nothing touches the network and `U` in the menu updates on demand (`TLWU_NO_UPDATE=1` forces it off). Japanese/Russian speech are not part of the Mac install. Untested on real Macs so far.

## Releases (read before trusting a binary)

The Windows release zip is unsigned and not reproducible; check its SHA256 or install from source. Details: [docs/install.md](docs/install.md#releases-read-before-trusting-a-binary).

## Languages

Focus languages are Japanese, English and Russian (Chinese is the base). Every other language still works for Listen, but is **experimental**: less tested, and menus / the GUI label it so.

| Language | Code | Status | Modes |
|---|---|---|---|
| Japanese | `ja` | focus | Listen, Speak, Meeting |
| English | `en` | focus | Listen, Speak, Meeting |
| Russian | `ru` | focus | Listen, Speak, Meeting |
| Korean, German, French, Spanish, Italian, Portuguese, Vietnamese, Thai, Indonesian | `ko` `de` `fr` `es` `it` `pt` `vi` `th` `id` | experimental | Listen only |

## Verification status

✅ verified on a real device, 🧪 not yet verified (links go to the test issue).

| | Windows | macOS | Linux |
|---|---|---|---|
| One-line install | ✅ | [🧪](https://github.com/sunososobro-hub/tinglongwu/issues/8) | ✅ |
| Listen | ✅ | [🧪](https://github.com/sunososobro-hub/tinglongwu/issues/8) | ✅ |
| Speak (virtual mic) | [🧪](https://github.com/sunososobro-hub/tinglongwu/issues/6) | [🧪](https://github.com/sunososobro-hub/tinglongwu/issues/8) | ✅ |
| Meeting | [🧪](https://github.com/sunososobro-hub/tinglongwu/issues/4) | [🧪](https://github.com/sunososobro-hub/tinglongwu/issues/8) | ✅ (own side) |
| Firewall-blocked offline | [🧪](https://github.com/sunososobro-hub/tinglongwu/issues/7) | [🧪](https://github.com/sunososobro-hub/tinglongwu/issues/8) | [🧪](https://github.com/sunososobro-hub/tinglongwu/issues/7) |
| exe | [🧪](https://github.com/sunososobro-hub/tinglongwu/issues/9) | — | — |

Windows modes verified on a real device so far: one-line install and Listen; Speak, Meeting and the firewall test are still pending (see the linked issues).

## Install / use split, privacy model

`tlwu-setup` is the only networked program; `tlwu` never downloads anything and runs with a network guard (defence in depth, not proof). Details and the firewall block test: [docs/privacy.md](docs/privacy.md).

## Install

Source install (Python 3.12, hash-verified pins), audio setup per OS, GPU: [docs/install.md](docs/install.md). Models, engines and pinned sources: [docs/models.md](docs/models.md).

## Usage
```
python -m tinglongwu                          # live system audio, auto language, Traditional Chinese
tlwu --lang ru --model small            # fixed source language
tlwu --file talk.mp3                    # wav/flac/ogg/mp3/m4a, decoded in memory
tlwu --zh-variant cn                    # Simplified output (default tw = Traditional, OpenCC s2twp)
tlwu --to ko --no-vad --chunk 8        # target Korean via ru->en->ko (needs en-ko), fixed blocks
tlwu --file talk.mp3 --transcript-only > hyp.txt     # source text only, one segment per line
python scripts/wer.py ref.txt hyp.txt --lang ru --partial   # WER (CER for zh/ja); --partial aligns to best reference window and reports coverage
```
Options: `--model tiny|base|small|medium|large-v3|large-v3-turbo`, `--quality auto|fast|balanced|quality` (speech model picked for this PC when `--model` is not given; default auto = recommended, see `--probe`), `--lang`, `--to`, `--preset meeting|chat` (default meeting), `--chunk` (max segment s, default from preset), , `--no-vad`, `--models-dir`, `--mic`, `--capture-device` (audio device substring), `--device auto|cpu|cuda` (compute), `--engine auto|qwen|qwen4b|opus|m2m`, `--no-merge`, `--max-wait` (seconds, default from preset), `--stream`, `--stream-src`, `--debug-latency`, `--glossary`, `--no-adapt` (keep the chosen models: by default live capture steps down the translation engine, then the speech model, when lines stay over 2 s late for 3 lines, and steps back up after a stable period).

**Streaming drafts** (`--stream`, default off, experimental): while a segment is still being spoken, tlwu re-runs Whisper (greedy) on the growing audio and immediately translates the whole partial text (same Qwen/OPUS engine), showing ONE dim line: the translation, in place. `--stream-src` also shows the source line (confirmed prefix bright, rest dim; LocalAgreement idea). `--debug-latency` logs to stderr the seconds from segment audio start to the first translated draft and to the final line. When the segment ends the final line replaces the draft. Needs an interactive terminal (ANSI); drafts yield to final ASR/MT, extra GPU load; with `--file` audio is paced in real time.

On GPUs with < 9 GB free VRAM (e.g. 8 GB cards that also hold Qwen3-8B) Whisper loads as int8_float16 (4x faster per pass than fp16 there; env `TLWU_ASR_COMPUTE` overrides). Stock Whisper subtitle-credit hallucinations on music/silence ("Продолжение следует", "Subtitles by", "字幕由"...; list in `tinglongwu/hallucination.py`) are dropped, and `妳` is always output as `你`.

**Sentence merging**: VAD segments often end mid-sentence. tlwu holds ASR text and translates at sentence ends (`.?!…。！？`), or when the speaker pauses for >= 0.8 s (VAD silence), or after `--max-wait` seconds / ~45 words as an upper bound, whichever comes first; a trailing "..." Whisper adds to a segment cut at the chunk limit is not treated as a sentence end, and words repeated across a cut are dropped when merging; `--no-merge` translates every segment as before. **Presets**: `--preset meeting` (default) = `--chunk 8 --max-wait 6`: prioritizes complete sentences, latency about 5-8 s; `--preset chat` = `--chunk 4 --max-wait 1.5`: lower latency but more sentence fragments (worse translation). Explicit `--chunk` / `--max-wait` override the preset; the GUI has a Mode selector. Reverse (speak-to-translate) mode has its own settings. Context: the Qwen engine sees the previous 2 lines (source + translation) so split sentences read coherently, and Whisper gets the previous text (<=100 chars) as `initial_prompt` (dropped after a repeat/garbage output). Qwen output is post-cleaned (stray `_`, markdown, quotes, "Translation:" labels). Ctrl+C exits within ~1 s.

## GUI (floating subtitles)

`tlwu-gui` (no console window) or `tlwu --gui` opens a small control window (source language, target zh-TW / zh-CN / en, engine, speech model, glossary file, "save transcript", Start/Stop) and a borderless always-on-top subtitle bar (bottom-centre, ~70% screen width). The last 2 lines are shown; the Chinese draft is grey and is replaced in place by the white final line. Drag to move; Ctrl+wheel or +/- changes the font size; right-click for: show/hide source line, font size, copy last line, pause display, quit. Settings are remembered in `%APPDATA%\tinglongwu\gui.json`. Pure stdlib tkinter, fully offline; the CLI is unchanged without `--gui`.

## Glossary (`--glossary PATH`)
Adapt to one topic with plain text files: `PATH` is a UTF-8 `.txt` file or a folder of `.txt` files. One entry per line, `source = target`; `#` starts a comment; a line without `=` is a plain hint term; `a | b = target` adds extra source spellings (e.g. common mis-hearings). Source terms go to Whisper as `hotwords`; entries whose source appears in the current sentence or the previous 2 lines (case-insensitive, Russian word endings tolerated) are added to the Qwen prompt as required translations (OPUS/M2M ignore targets). An entry like `2.4 = 2.4 GHz` also rewrites "2 и 4" / "2,4" to "2.4" in the ASR text. Explicit flag only, fully offline; the helper menus pass `--glossary` automatically when the user glossary folder (`%APPDATA%\tinglongwu\glossary`, `~/.config/tinglongwu/glossary`, macOS `~/Library/Application Support/tinglongwu/glossary`) holds `.txt` files. On a PC without a usable GPU and with Hy-MT2 installed, the menus' listen mode also asks whether to use Hy-MT2 instead of the OPUS pivot (better meaning, slower). Example: `docs/examples/glossary_wifi_ru.txt`.
```
tlwu --lang ru --model large-v3-turbo --engine qwen --stream --glossary docs/examples/glossary_wifi_ru.txt
```
Repetition loops (Whisper repeating one phrase): finals use temperature fallback, a detected loop is re-decoded once without the previous-text prompt, and any remaining repetition is collapsed to one copy before translation.

## Architecture
`tinglongwu/capture` (AudioSource: wasapi, pulse, macos, file, mic) -> `segment.py` (Silero VAD, 0.5 s overlap) -> `asr.py` (Recognizer) -> `translate.py` (Translator) -> `sink.py` (Sink; TextSink, TranscriptSink, TTSSink stub). Each is a small interface so parts can be swapped.

## Licenses and sources
Third-party components, their licenses and download sources: [docs/licenses.md](docs/licenses.md).

## Benchmarks
Translation quality and speed measurements: [docs/benchmarks.md](docs/benchmarks.md).

## Known limitations
- **Latency** is about segment length plus processing time: text appears after a pause (VAD) or at `--chunk` seconds. Use `--preset chat` (or a lower `--chunk`) for faster output at the cost of more half sentences.
- **English pivot** loses terminology and nuance (two lossy hops); gender, formality and proper nouns suffer.
- **Whisper hallucinates** on silence, music and noise; mitigated by silence gating, no-speech/logprob thresholds and `condition_on_previous_text=False`, not eliminated.
- **Auto language locks** after the first recognised segment; use `--lang` for mixed-language streams.
- **CPU speed vs size**: on CPU (int8) `medium`/`large-v3` may not keep up in real time; use `--device cuda` on an NVIDIA GPU.
- **macOS** needs a virtual loopback device (no native system-audio capture API without ScreenCaptureKit helper).
- **Traditional Chinese** is converted post-hoc with OpenCC from Simplified model output, so word choice is approximate.
- Live capture backends are implemented but only `--file` is covered by automated tests (no audio hardware in CI).

## Linux reverse mode (experimental, untested)
See [docs/linux-reverse.md](docs/linux-reverse.md).

## Acceptable Use / Consent
- Get the consent of every participant before recording, capturing or transcribing a call or meeting; recording and transcription laws vary by jurisdiction.
- Do not use this tool to circumvent DRM or other technical protection of content you have no right to copy.
- Do not use it to impersonate another person. Reverse mode speaks with fixed built-in voices only; there is no voice cloning, and you must not present synthesized speech as someone else's voice.

## Roadmap
Mic -> zh -> ru translation -> built-in-voice offline TTS (Vosk-TTS / Kokoro) -> virtual mic (VB-Cable / BlackHole / PipeWire null-sink), with echo exclusion so synthesized speech is not re-captured.

## Tests
`pip install -r requirements-dev.txt && pytest` (no models or network needed).

## Disclaimer
Provided as is (MIT, no warranty); output can be wrong; you are responsible for consent and local law. Full text: [docs/disclaimer.md](docs/disclaimer.md). Security: [SECURITY.md](SECURITY.md). Privacy: [PRIVACY.md](PRIVACY.md) ([繁體中文](PRIVACY.zh-TW.md)).

MIT License. See [LICENSE](LICENSE). (c) 2026 Alonso.
