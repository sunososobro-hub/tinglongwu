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

The Windows release zip is **unsigned** and built locally by the maintainer with Nuitka; the build is **not reproducible**. Its SHA256 is published in the release notes: compare it with `Get-FileHash tinglongwu-win64.zip` before running. The hash only proves you got the file the maintainer published. If you want real verification, install from source (`install.bat` or the steps below): the source, pinned dependencies and model hashes are all auditable.

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

- Audio lives in memory only. Transcripts go to stdout only; nothing is logged. Only the explicit opt-ins `--save` and `--save-result PATH` (speak mode) write text to disk.
- On `import tinglongwu` (before any HF/ctranslate2 import) the env is forced offline: `HF_HUB_OFFLINE`, `TRANSFORMERS_OFFLINE`, `HF_HUB_DISABLE_TELEMETRY`, `DO_NOT_TRACK`, `ARGOS_*`.
- A runtime **network guard** patches `socket.connect/connect_ex/create_connection/getaddrinfo/gethostbyname` and raises on any non-loopback target (loopback and AF_UNIX pass). It is on in `tlwu`, `tlwu --gui`, `python -m tinglongwu.reverse.proto` and the TTS worker subprocesses (Kokoro/Vosk). Only our own processes are affected; no OS firewall is touched. `tlwu-setup` and the update step (`git`) do not use the guard; `TLWU_ALLOW_NET=1` disables it.
- Network is for **install/setup** (tlwu-setup, pip) and **update** (git) only. Updating is opt-in: the first menu run asks once whether to check GitHub on every menu start (saved in the user config: `~/.config/tinglongwu/config`, `~/Library/Application Support/tinglongwu/config`, `%APPDATA%\tinglongwu\config`); if off, the menu never touches the network and `U` updates on demand. `TLWU_NO_UPDATE=1` forces it off.
- Every blocked attempt prints one line and appends a JSON line (time, host, port, mode, package guessed from the stack, trimmed stack without any content, tlwu version, OS) to `netguard.jsonl` in the user log dir (`~/.local/state/tinglongwu`, `~/Library/Logs/tinglongwu`, `%LOCALAPPDATA%\tinglongwu`). Nothing is ever sent automatically: the next menu start shows what was blocked and asks "report to the developer? [y/N]"; `y` shows the exact text and opens a pre-filled GitHub issue in your browser that you submit yourself.
- Menu language follows the system (English by default, Russian, Traditional Chinese); override with `TLWU_UI_LANG=en|ru|zh-TW` or `I` in the menu. Texts live in `scripts/i18n/` and `tinglongwu/i18n.py`; adding a language = adding a file.
- Models load only from a local `models/` dir (`--models-dir` or `TLWU_MODELS`); missing models give an error pointing to the download script. Nothing is auto-downloaded.
- `tlwu-setup` (`tinglongwu_setup`) is the **only** program that uses the network. It pins on first download (HF commit sha / package URL + SHA256 of every file; committed pins in `tinglongwu_setup/manifest.json`, local record in `models/manifest.json`) and verifies on every later run (`--verify` works offline).
- `scripts/verify_offline.py` runs tinglongWu and polls the whole process tree with psutil; fails on any non-loopback connection.
- The guard is in-process defence in depth, **not proof**. It only blocks Python-level sockets (`connect`, `connect_ex`, `sendto`, name resolution; our TTS worker subprocesses load the same guard). Native libraries that open their own sockets (ctranslate2, onnxruntime, llama.cpp, other C extensions) and other subprocesses are **not** covered. CI runs the tests inside an empty network namespace (`scripts/ci_offline.sh`) as an extra check. The firewall test below is the real, independent proof.

### Firewall block test (per platform)

One command runs listen, speak and meeting with the network cut and checks each still translates (Windows: admin terminal, adds and always removes an outbound block rule for the Python it uses; Linux: empty network namespace; macOS: block first, then add `--already-blocked`):
```
python scripts/firewall_test.py --listen-wav talk.wav --speak-wav zh.wav   # exit 0 = PASS
```
Add `--tts-python <kokoro venv python> --tts-model <kokoro dir>` to include speech synthesis. Manual steps:

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

Python 3.12 (the pinned numpy/scipy need >= 3.12; tested 3.12).
```
tlwu-setup --deps  # or: pip install -r requirements.txt && pip install -e . --no-deps    # exact pins
```

Hash-verified install (recommended): `requirements.lock.txt` lists every dependency (including transitive ones) with all PyPI SHA256 hashes, so pip refuses any tampered or substituted file:
```
pip install --require-hashes -r requirements.lock.txt && pip install -e . --no-deps
```
`requirements.txt` (plain `==` pins, no hashes) keeps working. Regenerate the lock with `python scripts/make_lock.py` (`httpx2`/`httpcore2` are not typosquats: they are the Pydantic-maintained httpx fork that `huggingface_hub` 2.x depends on).

Updating a Windows dev checkout (git clone on the `dev` branch): double-click **`update.bat`**: it fast-forwards `dev`, reinstalls the pinned packages and starts the GUI (stops with a message on local changes or another branch).

- **Windows**: nothing extra (WASAPI loopback of the default speakers).
- **Linux**: PipeWire (with pipewire-pulse) or PulseAudio. Default captures the monitor of the default sink; list with `pactl list sources short` and choose with `--capture-device <substring>`.
- **macOS**: install [BlackHole 2ch](https://github.com/ExistentialAudio/BlackHole), create a Multi-Output Device (speakers + BlackHole) in Audio MIDI Setup and make it the output. tlwu captures the device whose name contains `BlackHole`. ScreenCaptureKit audio capture is a possible future option.

### Models (one-time, needs network)
```
tlwu-setup --whisper small --langs ru --to zh
```
Translation uses OPUS-MT models as packaged by Argos Translate, run directly on CTranslate2 (no Argos/torch install). Route: source -> English -> Chinese; the English step is skipped when the source is English.

Any language pair with an OPUS-MT/Argos package works: `--langs` fetches source->English, `--to` fetches English->target (e.g. `--langs ru,ja --to zh,ko`). Fetch only what you need. If a pair is missing, tinglongWu stops and names the pair and the exact download command.

**Optional M2M-100 engine (`--engine m2m`)**: direct translation without the English pivot, **MIT licensed** (usable commercially). `tlwu-setup --m2m` downloads facebook/m2m100_1.2B and converts it with `ct2-transformers-converter`; the converter needs `torch` and `transformers`, which are NOT runtime dependencies: install them in a throwaway venv and point `TLWU_CONVERTER_PYTHON` at it. Loads from `models/m2m-1.2B/` only.  The conversion reads only `model.safetensors` (pinned HF commit of Hugging Face's own SFconvertbot PR, since `main` has only the pickle `pytorch_model.bin`), so no pickle is ever unpickled. `tlwu-setup` refuses to convert without that pinned safetensors revision.

**Japanese (`--lang ja`, auto candidates en,ru,ja,zh, `reverse.proto --to ja`)**: OPUS has no usable Japanese, so every pair with Japanese is translated directly (no English pivot) by Tencent Hy-MT2-1.8B (Apache-2.0, Q4_K_M GGUF, ~1.1 GB): `tlwu-setup --hymt` plus `llama-cpp-python` (CPU wheel is fine). Without it, listening falls back to the OPUS ja->en->zh pivot (poor); speaking Japanese requires it. Japanese speech is Kokoro-82M v1.0 (Apache-2.0) in the same venv as the English voice; it additionally needs `misaki==0.9.4 pyopenjtalk-plus fugashi unidic-lite mojimoji jaconv addict regex` there (see `tinglongwu/reverse/tts_worker.py`).

**Default GPU engine: Hy-MT2** (`--engine auto` on a usable NVIDIA GPU, or `--engine hymt`): Tencent Hy-MT2-1.8B (llama.cpp, Q4_K_M GGUF, `tlwu-setup --hymt`, ~1.1 GB download) translates directly source -> target for zh/en/ru/ja/ko/de/fr/es. On our 200-sentence benchmark it matches Qwen3-8B in quality (COMET x100: ru 86.5 vs 86.5, en 89.1 vs 88.8, ja 88.3 vs 87.8) at about 4x the speed (0.19 s vs 0.73 s per sentence) and ~1.5 GB VRAM instead of 5.1 GB, so Whisper turbo + Hy-MT2 fits a 4 GB card. `auto` needs ~2 GB of free VRAM after Whisper has loaded; if Hy-MT2 is not installed it falls back to the Qwen tiers below, then OPUS. On CPU the default stays OPUS.

**Qwen LLM engine (optional, `--engine qwen` / `qwen4b`; used by `auto` only when Hy-MT2 is not installed)**: a local Qwen3 model (llama.cpp, Q4_K_M GGUF) translates directly source -> Chinese (no English pivot, thinking mode off, repetition guarded), then OpenCC converts per `--zh-variant`. It is much more accurate than OPUS and does not produce garbage on very short lines. `tlwu-setup --qwen` downloads Qwen3-8B and Qwen3-4B (~7.5 GB) into the models dir; it also needs `llama-cpp-python` (CUDA build). `auto` checks free VRAM after Whisper has loaded: Qwen3-8B if >= 5.8 GB is free, else Qwen3-4B if >= 3.5 GB, else OPUS (also on CPU or if Qwen fails to load); one line at startup says which engine was chosen. Force one with `--engine qwen` (8B), `qwen4b`, `opus` or `m2m`.


#### Pinned model sources

`tinglongwu_setup/manifest.json` is committed: it pins every downloadable model to an exact Hugging Face commit and the SHA256 of each file. `tlwu-setup` downloads with `revision=<pinned commit>` and aborts on any hash mismatch; `tlwu-setup --verify` re-checks offline. Refresh pins with `scripts/make_model_pins.py` (maintainers; review the diff).

| Model | Source repo | Maintainer | Notes |
|---|---|---|---|
| Whisper tiny/base/small/medium/large-v3 (CT2) | `Systran/faster-whisper-*` | SYSTRAN | MIT |
| **Whisper large-v3-turbo (CT2)** | `mobiuslabsgmbh/faster-whisper-large-v3-turbo` | **mobiuslabsgmbh (third party, not OpenAI or SYSTRAN)** | MIT; a community CTranslate2 conversion, trust is limited to its pinned commit and hashes |
| Qwen3-8B / 4B Q4_K_M GGUF | `Qwen/Qwen3-*-GGUF` | Qwen (Alibaba) | Apache-2.0 |
| M2M-100 1.2B | `facebook/m2m100_1.2B` | Meta | MIT; **safetensors only** (see above) |
| OPUS-MT (Argos packages) | `argos-net.com` | Argos Open Tech | CC-BY 4.0; `ru-en` and `en-zh` are pinned by URL + archive SHA256; other pairs are looked up in the mutable Argos index on first use and only pinned afterwards (a warning is printed) |

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
