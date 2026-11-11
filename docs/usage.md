# Usage, GUI and glossary

[Back to README](../README.md)

## Overview

Fully local, offline, real-time speech translator: **system audio output -> capture -> ASR -> translate -> text**.
It captures whatever the PC plays (the system output mix, not per-app). There is no network use at runtime; only setup / first-run model download uses the network.
Nothing leaves your machine: no cloud APIs, no telemetry, no audio or transcripts written to disk.

The original tagline: tinglongWu — Taiwanese 聽攏有 (thiann lóng ū): "I understood it all". Real-time speech translation, offline at runtime and verifiable (install and model download need the network; the in-process guard is defence in depth, not proof, see Privacy model).

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
Adapt to one topic with plain text files: `PATH` is a UTF-8 `.txt` file or a folder of `.txt` files. One entry per line, `source = target`; `#` starts a comment; a line without `=` is a plain hint term; `a | b = target` adds extra source spellings (e.g. common mis-hearings). Source terms go to Whisper as `hotwords`; entries whose source appears in the current sentence or the previous 2 lines (case-insensitive, Russian word endings tolerated) are added to the Qwen prompt as required translations (OPUS/M2M ignore targets). An entry like `2.4 = 2.4 GHz` also rewrites "2 и 4" / "2,4" to "2.4" in the ASR text. Explicit flag only, fully offline. Example: `examples/glossary_wifi_ru.txt`.
```
tlwu --lang ru --model large-v3-turbo --engine qwen --stream --glossary examples/glossary_wifi_ru.txt
```
Repetition loops (Whisper repeating one phrase): finals use temperature fallback, a detected loop is re-decoded once without the previous-text prompt, and any remaining repetition is collapsed to one copy before translation.

## Architecture
`tinglongwu/capture` (AudioSource: wasapi, pulse, macos, file, mic) -> `segment.py` (Silero VAD, 0.5 s overlap) -> `asr.py` (Recognizer) -> `translate.py` (Translator) -> `sink.py` (Sink; TextSink, TranscriptSink, TTSSink stub). Each is a small interface so parts can be swapped.
