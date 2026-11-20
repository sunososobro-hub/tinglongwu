# Usage, GUI, glossary, limitations

Moved from the README (#166). Quick start commands (menu, live, file, GUI) are in the section below.

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
Options: `--model tiny|base|small|medium|large-v3|large-v3-turbo`, `--quality auto|fast|balanced|quality` (speech model picked for this PC when `--model` is not given; default auto = recommended, see `--probe`), `--lang`, `--to`, `--preset meeting|chat` (default meeting), `--chunk` (max segment s, default from preset), , `--no-vad`, `--models-dir`, `--mic`, `--capture-device` (audio device substring), `--device auto|cpu|cuda` (compute), `--engine auto|hymt|hymt7b|opus|ollama|fm` (ollama / fm are opt-in, see [models](models.md)), `--ollama-model`, `--ollama-url`, `--no-merge`, `--max-wait` (seconds, default from preset), `--stream`, `--stream-src`, `--debug-latency`, `--glossary`, `--no-adapt` (keep the chosen models: by default live capture steps down the translation engine, then the speech model, when lines stay over 2 s late for 3 lines, and steps back up after a stable period).

**Streaming drafts** (`--stream`, default off, experimental): while a segment is still being spoken, tlwu re-runs Whisper (greedy) on the growing audio and immediately translates the whole partial text (same translation engine), showing ONE dim line: the translation, in place. `--stream-src` also shows the source line (confirmed prefix bright, rest dim; LocalAgreement idea). `--debug-latency` logs to stderr the seconds from segment audio start to the first translated draft and to the final line. When the segment ends the final line replaces the draft. Needs an interactive terminal (ANSI); drafts yield to final ASR/MT, extra GPU load; with `--file` audio is paced in real time.

On GPUs with < 9 GB free VRAM (e.g. 8 GB cards that also hold the translation LLM) Whisper loads as int8_float16 (4x faster per pass than fp16 there; env `TLWU_ASR_COMPUTE` overrides). Stock Whisper subtitle-credit hallucinations on music/silence ("Продолжение следует", "Subtitles by", "字幕由"...; list in `tinglongwu/hallucination.py`) are dropped, and `妳` is always output as `你`.

**Sentence merging**: VAD segments often end mid-sentence. tlwu holds ASR text and translates at sentence ends (`.?!…。！？`), or when the speaker pauses for >= 0.8 s (VAD silence), or after `--max-wait` seconds / ~45 words as an upper bound, whichever comes first; a trailing "..." Whisper adds to a segment cut at the chunk limit is not treated as a sentence end, and words repeated across a cut are dropped when merging; `--no-merge` translates every segment as before. **Presets**: `--preset meeting` (default) = `--chunk 8 --max-wait 6`: prioritizes complete sentences, latency about 5-8 s; `--preset chat` = `--chunk 4 --max-wait 1.5`: lower latency but more sentence fragments (worse translation). Explicit `--chunk` / `--max-wait` override the preset; the GUI has a Mode selector. Reverse (speak-to-translate) mode has its own settings. Context: Hy-MT2 translates each sentence on its own (no context); the opt-in ollama / fm engines see the previous 2 lines (source + translation) so split sentences read coherently, and Whisper gets the previous text (<=100 chars) as `initial_prompt` (dropped after a repeat/garbage output). LLM output (Hy-MT2, ollama, fm) is post-cleaned (stray `_`, markdown, quotes, "Translation:" labels). Ctrl+C exits within ~1 s.

**Room sound (lecture, in-person meeting)**: `tlwu --mic` listens to the microphone instead of what the PC plays; `--capture-device "USB"` picks a microphone by name substring (default: the system default microphone). In the GUI pick the "Watching video (listen only)" scene, then Adjust > Advanced > Audio source = Microphone and, optionally, "Microphone (room sound)" = the one to use. The meeting scene always listens to PC sound, because there the microphone is your own speak side. Same offline path as PC sound: nothing is sent anywhere. Put the microphone near the speaker (a laptop mic 2-3 m away picks up more room echo and noise, so expect more errors). When the PC also plays the translation aloud (voice output), use a headset: from speakers the mic hears the spoken translation again.

## GUI (floating subtitles)

`tlwu-gui` (no console window) or `tlwu --gui` opens a small control window (source language, target zh-TW / zh-CN / en, engine, speech model, glossary file, "save transcript", Start/Stop) and a borderless always-on-top subtitle bar (bottom-centre, ~70% screen width). The last 2 lines are shown; the Chinese draft is grey and is replaced in place by the white final line. Drag to move; Ctrl+wheel or +/- changes the font size; right-click for: show/hide source line, font size, copy last line, pause display, quit. Settings are remembered in `%APPDATA%\tinglongwu\gui.json`. Pure stdlib tkinter, fully offline; the CLI is unchanged without `--gui`.

Opt-in Hy-MT2 7B (stronger on Russian as the source, needs a GPU with >= 8 GB): Advanced > Quality: Manual > Engine: hymt7b, after `tlwu-setup --hymt7b`; see [models](models.md).

Auto-update (on by default) checks GitHub once a day for a new version, with no ID or personal data; untick it in setup or set `TLWU_OFFLINE=1` for zero network requests ([details](privacy.md)). Translation, audio and subtitles never use the network.

### Meeting notes (offline)

Settings > **Meeting notes** is one switch. The first time you turn it on, tlwu asks to download the notes model (Qwen3-4B-Instruct-2507, about 2.5 GB, SHA256-pinned) with the same progress bar as language packs; **Cancel download** in the notes window stops it and the switch goes back off. After that no network is used. While you listen, each finished topic (title, key points, action items) appears in the notes window and is written to `<transcript>.topics.md` next to the saved transcript; the switch saves the transcript by itself. Notes are written in the language you read (your "Translate to" language), and the window says which. The notes model runs in one low-priority background thread, so subtitles never wait for it.

On by default only with an NVIDIA GPU. On a CPU-only PC it is off by default: there a 5-minute topic takes about 4 minutes to summarize ([benchmarks](benchmarks.md#offline-topic-notes-default-model-bake-off-1378-595-610)), so the notes arrive after the meeting; turn it on if that is fine. Settings > **Model (.gguf)...** in the notes window picks your own GGUF instead. CLI: `tlwu --save --topics MODEL.gguf`.

## Glossary (`--glossary PATH`)
Adapt to one topic with plain text files: `PATH` is a UTF-8 `.txt` file or a folder of `.txt` files. One entry per line, `source = target`; `#` starts a comment; a line without `=` is a plain hint term; `a | b = target` adds extra source spellings (e.g. common mis-hearings). Source terms go to Whisper as `hotwords`; when the target is Chinese, entries whose source appears in the current sentence or the previous 2 lines (case-insensitive, Russian word endings tolerated) are added to the Hy-MT2 prompt in the model card's terminology-reference form (`tinglongwu/hymt.py` `HyMtTranslator._terms` -> `build_prompt`); for other targets Hy-MT2 does not see the targets. Chinese output is also corrected with each entry's `~` list of known wrong renderings (Hy-MT2 and the OPUS pivot). The opt-in ollama / fm engines get the same entries as required translations for every target. An entry like `2.4 = 2.4 GHz` also rewrites "2 и 4" / "2,4" to "2.4" in the ASR text. Explicit flag only, fully offline; the helper menus pass `--glossary` automatically when the user glossary folder (`%APPDATA%\tinglongwu\glossary`, `~/.config/tinglongwu/glossary`, macOS `~/Library/Application Support/tinglongwu/glossary`) holds `.txt` files. On a PC without a usable GPU and with Hy-MT2 installed, the menus' listen mode also asks whether to use Hy-MT2 instead of the OPUS pivot (better meaning, slower). Example: `docs/examples/glossary_wifi_ru.txt`. No idea what to put in it? [Make one with your own AI](glossary-with-ai.md).
```
tlwu --lang ru --model large-v3-turbo --stream --glossary docs/examples/glossary_wifi_ru.txt
```
Repetition loops (Whisper repeating one phrase): finals use temperature fallback, a detected loop is re-decoded once without the previous-text prompt, and any remaining repetition is collapsed to one copy before translation.

## Architecture
`tinglongwu/capture` (AudioSource: wasapi, pulse, macos, file, mic) -> `segment.py` (Silero VAD, 0.5 s overlap) -> `asr.py` (Recognizer) -> `translate.py` (Translator) -> `sink.py` (Sink; TextSink, TranscriptSink, TTSSink stub). Each is a small interface so parts can be swapped.

## Known limitations
- **Latency** is about segment length plus processing time: text appears after a pause (VAD) or at `--chunk` seconds. Use `--preset chat` (or a lower `--chunk`) for faster output at the cost of more half sentences.
- **English pivot** loses terminology and nuance (two lossy hops); gender, formality and proper nouns suffer.
- **Whisper hallucinates** on silence, music and noise; mitigated by silence gating, no-speech/logprob thresholds and `condition_on_previous_text=False`, not eliminated.
- **Auto language locks** after the first recognised segment; use `--lang` for mixed-language streams.
- **CPU speed vs size**: on CPU (int8) `medium`/`large-v3` may not keep up in real time; use `--device cuda` on an NVIDIA GPU.
- **macOS** needs a virtual loopback device (no native system-audio capture API without ScreenCaptureKit helper).
- **Traditional Chinese** is converted post-hoc with OpenCC from Simplified model output, so word choice is approximate.
- Live capture backends are implemented but only `--file` is covered by automated tests (no audio hardware in CI).

## Minimum spec
README "System requirements" is the supported spec. At startup a CPU without AVX2 exits with `tlwu: not supported: CPU lacks AVX2` (`--force` continues anyway; an unknown answer, e.g. Apple Silicon, never blocks). On CPU only, after the first 3 segments, if the (ASR + MT) p50 per segment is more than half the segment length, one warning says this machine is below the recommended spec and live subtitles will lag.

## Roadmap
Mic -> zh -> ru translation -> built-in-voice offline TTS (Vosk-TTS / Kokoro) -> virtual mic (VB-Cable / BlackHole / PipeWire null-sink), with echo exclusion so synthesized speech is not re-captured.
