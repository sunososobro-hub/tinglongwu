# tinglongWu — Taiwanese 聽攏有 (thiann lóng ū): "I understood it all". Real-time speech translation, 100% offline and verifiable.

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

## Quick install (Windows)

For Windows 10/11 with an NVIDIA GPU (works without one, just slower). You need [Python 3.12](https://www.python.org/downloads/windows/) first (tick **Add python.exe to PATH** in its installer).

1. On GitHub click **Code > Download ZIP**, then right-click the ZIP > **Extract All**.
2. Open the folder and double-click **`install.bat`**. Wait (10-30 min, up to ~10 GB download; safe to re-run if interrupted).
3. Double-click the new desktop shortcut **聽攏有 tinglongWu**.

To remove: delete the folder and the shortcut. `install.ps1 -DryRun` only shows what would be done. The Qwen engine uses `requirements-qwen.txt` (CUDA build of llama-cpp-python); if no matching wheel exists the installer falls back to the OPUS engine automatically.

## Install / use split (read this first)

One repo, two programs with a hard boundary:

| | `tlwu-setup` (package `tinglongwu_setup`) | `tlwu` (package `tinglongwu`) |
|---|---|---|
| Network | **Yes** - the only networked code | **Never** - zero download/network code |
| Does | installs pinned deps, downloads + SHA256-pins models, builds/installs offline bundles | captures audio, transcribes, translates, prints |
| When | once, while online | every run, can be fully offline |

`tests/test_no_network_code.py` statically scans `tinglongwu/` and fails if it imports requests, urllib.request, http.client, huggingface_hub, argostranslate, socket (except the guard module `tinglongwu/offline.py`) or `tinglongwu_setup`.

```
tlwu-setup --deps                                  # pip install the pinned requirements
tlwu-setup --whisper small --langs ru --to zh      # download + pin models
tlwu-setup --bundle tlwu-bundle.zip                 # pack wheels + models for an air-gapped machine
tlwu-setup --from-bundle tlwu-bundle.zip            # on the offline machine: install + verify, no network
```
Bundles are platform-specific: build on the same OS, CPU architecture and Python minor version as the target.

## Privacy model

- Audio lives in memory only. Transcripts go to stdout only; nothing is logged.
- On `import tinglongwu` (before any HF/ctranslate2 import) the env is forced offline: `HF_HUB_OFFLINE`, `TRANSFORMERS_OFFLINE`, `HF_HUB_DISABLE_TELEMETRY`, `DO_NOT_TRACK`, `ARGOS_*`.
- A runtime **network guard** patches `socket.connect/connect_ex/getaddrinfo/gethostbyname` and raises on any non-loopback target. It is always on.
- Models load only from a local `models/` dir (`--models-dir` or `TLWU_MODELS`); missing models give an error pointing to the download script. Nothing is auto-downloaded.
- `tlwu-setup` (`tinglongwu_setup`) is the **only** program that uses the network. It pins on first download (HF commit sha / package URL + SHA256 of every file, stored in `models/manifest.json`) and verifies on every later run (`--verify` works offline).
- `scripts/verify_offline.py` runs tinglongWu and polls the whole process tree with psutil; fails on any non-loopback connection.
- The guard is in-process defence in depth; the firewall test below is the independent proof.

### Firewall block test (per platform)

Windows (admin PowerShell), block outbound for the Python you use:
```powershell
New-NetFirewallRule -DisplayName "tlwu-block" -Direction Outbound -Action Block -Program (Get-Command python).Source
python -m tinglongwu --file talk.wav      # must still work
Remove-NetFirewallRule -DisplayName "tlwu-block"
```
Linux: `unshare -n -r python -m tinglongwu --file talk.wav` (no network namespace; live capture via a unix socket still works only if PulseAudio is reachable - use `--file` for the test), or an nftables rule: `nft add rule inet filter output meta skuid $UID drop`.
macOS: `pf` rule (`block drop out quick all user $USER` loaded with `pfctl -f`), or Little Snitch / LuLu set to deny the Python binary.

Then `python scripts/verify_offline.py --file talk.wav`.

## Install

Python 3.10+ (tested 3.12).
```
tlwu-setup --deps  # or: pip install -r requirements.txt && pip install -e . --no-deps    # exact pins
```
- **Windows**: nothing extra (WASAPI loopback of the default speakers).
- **Linux**: PipeWire (with pipewire-pulse) or PulseAudio. Default captures the monitor of the default sink; list with `pactl list sources short` and choose with `--capture-device <substring>`.
- **macOS**: install [BlackHole 2ch](https://github.com/ExistentialAudio/BlackHole), create a Multi-Output Device (speakers + BlackHole) in Audio MIDI Setup and make it the output. tlwu captures the device whose name contains `BlackHole`. ScreenCaptureKit audio capture is a possible future option.

### Models (one-time, needs network)
```
tlwu-setup --whisper small --langs ru --to zh
```
Translation uses OPUS-MT models as packaged by Argos Translate, run directly on CTranslate2 (no Argos/torch install). Route: source -> English -> Chinese; the English step is skipped when the source is English.

Any language pair with an OPUS-MT/Argos package works: `--langs` fetches source->English, `--to` fetches English->target (e.g. `--langs ru,ja --to zh,ko`). Fetch only what you need. If a pair is missing, tinglongWu stops and names the pair and the exact download command.

**Optional M2M-100 engine (`--engine m2m`)**: direct translation without the English pivot, **MIT licensed** (usable commercially). `tlwu-setup --m2m` downloads facebook/m2m100_1.2B and converts it with `ct2-transformers-converter`; the converter needs `torch` and `transformers`, which are NOT runtime dependencies: install them in a throwaway venv and point `TLWU_CONVERTER_PYTHON` at it. Loads from `models/m2m-1.2B/` only.

**Qwen LLM engine (default `--engine auto`)**: a local Qwen3 model (llama.cpp, Q4_K_M GGUF) translates directly source -> Chinese (no English pivot, thinking mode off, repetition guarded), then OpenCC converts per `--zh-variant`. It is much more accurate than OPUS and does not produce garbage on very short lines. `tlwu-setup --qwen` downloads Qwen3-8B and Qwen3-4B (~7.5 GB) into the models dir; it also needs `llama-cpp-python` (CUDA build). `auto` checks free VRAM after Whisper has loaded: Qwen3-8B if >= 5.8 GB is free, else Qwen3-4B if >= 3.5 GB, else OPUS (also on CPU or if Qwen fails to load); one line at startup says which engine was chosen. Force one with `--engine qwen` (8B), `qwen4b`, `opus` or `m2m`.


### GPU (optional, NVIDIA)
`--device auto|cpu|cuda` (default `auto`: CUDA if CTranslate2 sees an NVIDIA GPU and the libraries load, else CPU). GPU runs Whisper in float16 and OPUS/M2M in int8_float16; CPU uses int8. What you need, once (with network):
```
tlwu-setup --gpu     # = pip install nvidia-cublas-cu12 nvidia-cudnn-cu12 (CUDA 12 cuBLAS + cuDNN 9; no CUDA toolkit needed)
```
plus an NVIDIA driver that supports CUDA 12 or newer. If the GPU or these libraries are missing, tlwu prints a clear message and falls back to CPU (with `--device cuda` too). Models never leave your machine; the libraries are only loaded locally. `large-v3` in float16 needs about 4 GB of VRAM, `medium` about 2.5 GB.

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
Options: `--model tiny|base|small|medium|large-v3|large-v3-turbo`, `--lang`, `--to`, `--chunk` (max segment s, default 4), `--no-vad`, `--models-dir`, `--mic`, `--capture-device` (audio device substring), `--device auto|cpu|cuda` (compute), `--engine auto|qwen|qwen4b|opus|m2m`, `--no-merge`, `--max-wait` (seconds), `--stream`, `--stream-src`, `--debug-latency`, `--glossary`.

**Streaming drafts** (`--stream`, default off, experimental): while a segment is still being spoken, tlwu re-runs Whisper (greedy) on the growing audio and immediately translates the whole partial text (same Qwen/OPUS engine), showing ONE dim line: the translation, in place. `--stream-src` also shows the source line (confirmed prefix bright, rest dim; LocalAgreement idea). `--debug-latency` logs to stderr the seconds from segment audio start to the first translated draft and to the final line. When the segment ends the final line replaces the draft. Needs an interactive terminal (ANSI); drafts yield to final ASR/MT, extra GPU load; with `--file` audio is paced in real time.

On GPUs with < 9 GB free VRAM (e.g. 8 GB cards that also hold Qwen3-8B) Whisper loads as int8_float16 (4x faster per pass than fp16 there; env `TLWU_ASR_COMPUTE` overrides). Stock Whisper subtitle-credit hallucinations on music/silence ("Продолжение следует", "Subtitles by", "字幕由"...; list in `tinglongwu/hallucination.py`) are dropped, and `妳` is always output as `你`.

**Sentence merging**: VAD segments often end mid-sentence. tlwu holds ASR text and translates at sentence ends (`.?!…。！？`), or after `--max-wait` seconds (default 1.5) or ~45 words, whichever comes first; `--no-merge` translates every segment as before. Live mode adds at most `--max-wait` seconds of delay to text that ends mid-sentence, none when a sentence already ended. Defaults are tuned for low latency: `--chunk 4`. Context: the Qwen engine sees the previous 2 lines (source + translation) so split sentences read coherently, and Whisper gets the previous text (<=100 chars) as `initial_prompt` (dropped after a repeat/garbage output). Qwen output is post-cleaned (stray `_`, markdown, quotes, "Translation:" labels). Ctrl+C exits within ~1 s.

## GUI (floating subtitles)

`tlwu-gui` (no console window) or `tlwu --gui` opens a small control window (source language, target zh-TW / zh-CN / en, engine, speech model, glossary file, "save transcript", Start/Stop) and a borderless always-on-top subtitle bar (bottom-centre, ~70% screen width). The last 2 lines are shown; the Chinese draft is grey and is replaced in place by the white final line. Drag to move; Ctrl+wheel or +/- changes the font size; right-click for: show/hide source line, font size, copy last line, pause display, quit. Settings are remembered in `%APPDATA%\tinglongwu\gui.json`. Pure stdlib tkinter, fully offline; the CLI is unchanged without `--gui`.

## Glossary (`--glossary PATH`)
Adapt to one topic with plain text files: `PATH` is a UTF-8 `.txt` file or a folder of `.txt` files. One entry per line, `source = target`; `#` starts a comment; a line without `=` is a plain hint term; `a | b = target` adds extra source spellings (e.g. common mis-hearings). Source terms go to Whisper as `hotwords`; entries whose source appears in the current sentence or the previous 2 lines (case-insensitive, Russian word endings tolerated) are added to the Qwen prompt as required translations (OPUS/M2M ignore targets). An entry like `2.4 = 2.4 GHz` also rewrites "2 и 4" / "2,4" to "2.4" in the ASR text. Explicit flag only, fully offline. Example: `docs/examples/glossary_wifi_ru.txt`.
```
tlwu --lang ru --model large-v3-turbo --engine qwen --stream --glossary docs/examples/glossary_wifi_ru.txt
```
Repetition loops (Whisper repeating one phrase): finals use temperature fallback, a detected loop is re-decoded once without the previous-text prompt, and any remaining repetition is collapsed to one copy before translation.

## Architecture
`tinglongwu/capture` (AudioSource: wasapi, pulse, macos, file, mic) -> `segment.py` (Silero VAD, 0.5 s overlap) -> `asr.py` (Recognizer) -> `translate.py` (Translator) -> `sink.py` (Sink; TextSink, TranscriptSink, TTSSink stub). Each is a small interface so parts can be swapped.

## Licenses and sources
| Component | License | Source |
|---|---|---|
| tinglongwu | MIT | this repo |
| faster-whisper | MIT | github.com/SYSTRAN/faster-whisper |
| Whisper weights (Systran CT2 conversions) | MIT | huggingface.co/Systran |
| Silero VAD (bundled ONNX in faster-whisper; Silero Team) | MIT | github.com/snakers4/silero-vad |
| CTranslate2 | MIT | github.com/OpenNMT/CTranslate2 |
| OPUS-MT models (Helsinki-NLP; attribution required) | CC-BY 4.0 | github.com/Helsinki-NLP/Opus-MT, argosopentech/argospm-index |
| Argos Translate (model packaging / index) | MIT | github.com/argosopentech/argos-translate |
| SentencePiece | Apache-2.0 | github.com/google/sentencepiece |
| OpenCC (opencc-python-reimplemented) | Apache-2.0 | github.com/yichen0831/opencc-python |
| soundcard | BSD-3 | github.com/bastibe/SoundCard |
| soundfile / libsndfile | BSD-3 / LGPL-2.1 | github.com/bastibe/python-soundfile |
| PyAV (FFmpeg libs; source install only, the Windows exe stubs `av` out) | BSD-3 / LGPL | github.com/PyAV-Org/PyAV |
| numpy, scipy, onnxruntime | BSD-3, BSD-3, MIT | |
| psutil, huggingface_hub | BSD-3, Apache-2.0 | |
| NLLB-200 | CC-BY-NC 4.0 (non-commercial) | not shipped, no code or setup option |
| Piper TTS (roadmap only, not shipped) | GPL-3.0 | github.com/OHF-Voice/piper1-gpl |

Verify upstream licenses yourself before redistributing models.

## Benchmarks

Translation quality and speed, measured with `scripts/bench/` (dev tool, not part of the product).

- Hardware: RTX 3050 8 GB. Data: FLORES-200 devtest (1012 sentences, Russian -> Traditional Chinese, gold source text; the 50-sentence rows use the first 50 sentences). Metrics: chrF (sacrebleu) and COMET (Unbabel/wmt22-comet-da). Latency excludes model load. Qwen engines are Q4_K_M GGUF, 2048 context.

| engine | chrF | COMET | median s/sentence | peak VRAM (+MiB) |
|---|---|---|---|---|
| opus (default, CPU/GPU) | 23.6 | 0.824 | 0.14 | 424 |
| m2m | 22.9 | 0.819 | 0.34 | 1724 |
| qwen3-4b | 23.5 | 0.849 | 0.46 | 3062 |
| qwen25-7b | 23.5 | 0.840 | 0.72 | 4682 |
| madlad3b | 26.1 | 0.846 | 0.69 | 3552 |
| qwen3-8b | 26.0 | 0.865 | 0.77 | 5148 |
| qwen3-8b + context | 26.3 | 0.867 | 0.80 | 5150 |
| Google Cloud Translation v2 (reference) | 29.8 | 0.879 | - | - |

| direction (first 50 sentences) | engine | chrF | COMET | median s/sentence |
|---|---|---|---|---|
| en->zho_Hant | opus | 29.9 | 0.854 | 0.10 |
| en->zho_Hant | qwen3-4b | 30.7 | 0.881 | 0.54 |
| en->zho_Hant | qwen3-8b | 31.2 | 0.892 | 0.91 |
| en->zho_Hant | Google | 37.2 | 0.900 | - |
| ru->eng | opus | 62.2 | 0.867 | 0.07 |
| ru->eng | qwen3-4b | 62.9 | 0.867 | 0.52 |
| ru->eng | qwen3-8b | 64.3 | 0.870 | 0.81 |
| ru->eng | Google | 67.2 | 0.883 | - |

A 200-utterance FLEURS speech run (ASR large-v3-turbo -> MT) ranks the engines the same way (opus 22.7, qwen3-8b 24.9, qwen3-4b 22.3, Google 28.5 chrF).

To reproduce (needs the bench venv, the data under `%USERPROFILE%/tlwu-eval`, and for the Google column a Cloud Translation key in `google_key.txt`; use `--no-google` to skip it):

```
.venv-bench\Scripts\python scripts/bench/run.py --full --engines m2m,qwen3-8b,qwen3-8b+ctx,madlad3b,qwen3-4b,qwen25-7b
.venv-bench\Scripts\python scripts/bench/run.py --smoke --pair en-zh --engines qwen3-8b,qwen3-4b
.venv-bench\Scripts\python scripts/bench/run.py --smoke --pair ru-en --engines qwen3-8b,qwen3-4b
```

**Google scores higher than every offline engine** in every direction we tested. The offline engines trade some quality for working with no network and no data leaving the machine. Small samples (50 sentences) are indicative only.

## Known limitations
- **Latency** is about segment length plus processing time: text appears after a pause (VAD) or at `--chunk` seconds. Lower `--chunk` for faster output at lower accuracy.
- **English pivot** loses terminology and nuance (two lossy hops); gender, formality and proper nouns suffer.
- **Whisper hallucinates** on silence, music and noise; mitigated by silence gating, no-speech/logprob thresholds and `condition_on_previous_text=False`, not eliminated.
- **Auto language locks** after the first recognised segment; use `--lang` for mixed-language streams.
- **CPU speed vs size**: on CPU (int8) `medium`/`large-v3` may not keep up in real time; use `--device cuda` on an NVIDIA GPU.
- **macOS** needs a virtual loopback device (no native system-audio capture API without ScreenCaptureKit helper).
- **Traditional Chinese** is converted post-hoc with OpenCC from Simplified model output, so word choice is approximate.
- Live capture backends are implemented but only `--file` is covered by automated tests (no audio hardware in CI).

## Consent
Get consent from participants before capturing calls or meetings; recording and transcription laws vary by jurisdiction.

## Roadmap
Mic -> zh -> ru translation -> Piper offline TTS -> virtual mic (VB-Cable / BlackHole / PipeWire null-sink), with echo exclusion so synthesized speech is not re-captured.

## Tests
`pip install -r requirements-dev.txt && pytest` (no models or network needed).

MIT License. See [LICENSE](LICENSE). (c) 2026 Alonso.
