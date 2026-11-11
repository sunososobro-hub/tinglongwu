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
2. Open the folder and double-click **`install.bat`**. Wait (10-30 min, up to ~10 GB download; safe to re-run if interrupted).
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

Python 3.10+ (tested 3.12).
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
| Component | License | Source |
|---|---|---|
| tinglongwu | MIT | this repo |
| faster-whisper | MIT | github.com/SYSTRAN/faster-whisper |
| Whisper weights (Systran CT2 conversions) | MIT | huggingface.co/Systran |
| Whisper large-v3-turbo weights (third-party CT2 conversion by mobiuslabsgmbh) | MIT | huggingface.co/mobiuslabsgmbh/faster-whisper-large-v3-turbo |
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
| Qwen3 LLM (GGUF; official Qwen repos) | Apache-2.0 | huggingface.co/Qwen/Qwen3-4B-GGUF (license from the model card metadata) |
| llama-cpp-python (runs Qwen3 / Hy-MT2) | MIT | github.com/abetlen/llama-cpp-python |
| Hy-MT2-1.8B GGUF (Tencent; Japanese MT) | Apache-2.0 (LICENSE.txt in the repo is the plain Apache-2.0 text, no extra restrictions) | huggingface.co/tencent/Hy-MT2-1.8B-GGUF |
| Vosk-TTS (code; Russian voice) | Apache-2.0 | github.com/alphacep/vosk-tts |
| Vosk-TTS Russian voice model (vosk-model-tts-ru-0.7-multi; **optional, opt-in**: never downloaded by default, only via `tlwu-setup --vosk-tts-ru` or your own download) | **Unclear: upstream states no license for the model** (only the vosk-tts code repo is Apache-2.0; no model license found on alphacephei.com or in the repo; the upstream README now lists 0.9-multi). Treat as unknown; users are responsible. Without it Russian speech falls back to subtitles only | github.com/alphacep/vosk-tts, alphacephei.com/vosk/models |
| kokoro-onnx (English/Japanese voice) | MIT | github.com/thewh1teagle/kokoro-onnx |
| Kokoro voice model (Kokoro-82M, ONNX) | Apache-2.0 (as stated by kokoro-onnx) | github.com/thewh1teagle/kokoro-onnx |
| espeak-ng (shipped inside `espeakng-loader`, pulled in by kokoro-onnx; Kokoro venv only, never in the main process or the exe) | GPL-3.0-or-later | github.com/espeak-ng/espeak-ng |
| espeakng-loader (pulled in by kokoro-onnx) | MIT (it loads the espeak-ng GPL libraries) | github.com/thewh1teagle/espeakng-loader |
| phonemizer (pulled in by kokoro-onnx 0.6.1; Kokoro venv only) | GPL-3.0 (PyPI metadata) | github.com/bootphon/phonemizer |
| misaki (G2P for Kokoro, incl. Japanese) | Apache-2.0 | github.com/hexgrad/misaki |
| pyopenjtalk-plus, fugashi, jaconv, mojimoji, addict, regex, unidic-lite (misaki[ja] extras, Kokoro venv only; you install them) | MIT, MIT AND BSD-3-Clause, MIT, Apache-2.0, MIT, Apache-2.0 AND CNRI-Python, MIT respectively (all from PyPI metadata; the UniDic dictionary data inside unidic-lite was not checked separately) | pypi.org/project/<name> |
| GigaAM v3 (Russian ASR, mobile app only; ONNX export by k2-fsa/sherpa-onnx, you side-load it) | MIT (GigaAM repo LICENSE and the ai-sage/GigaAM-v3 model card; the sherpa-onnx export card points to the same GigaAM LICENSE) | github.com/salute-developers/GigaAM, huggingface.co/ai-sage/GigaAM-v3 |
| sherpa-onnx (mobile app only) | Apache-2.0 | github.com/k2-fsa/sherpa-onnx |
| llama.cpp (self-built AAR in `mobile/`) | MIT (per mobile/README) | github.com/ggml-org/llama.cpp |
| BlackHole (macOS virtual audio device) | GPL-3.0; Existential Audio requires a separate license for non-GPL-3.0 projects | github.com/ExistentialAudio/BlackHole. **You install it (`mac_install.sh` calls Homebrew on your machine); this project does not bundle or redistribute it.** |
| VB-CABLE (Windows virtual audio cable) | Donationware, proprietary (VB-Audio); redistribution needs a license from VB-Audio | vb-audio.com/Cable. **You install it; this project does not bundle or redistribute it.** |

Verify upstream licenses yourself before redistributing models.

Notes on the table: (1) No GPL component is bundled in this repository or in the Windows exe (the exe build excludes reverse mode, so Kokoro, espeak-ng and phonemizer are not in it). The GPL pieces (espeak-ng, phonemizer, pulled in by kokoro-onnx) are only installed by you with pip into a separate Kokoro venv and are used by a separate worker process, never imported by the main program. (2) Nothing shipped or downloaded by `tlwu-setup` is non-commercial; NLLB-200 (CC-BY-NC) is the only NC model mentioned and it is not shipped. (3) The Vosk-TTS Russian voice model has no stated license upstream: treat it as unknown. This is a non-commercial research project and you are responsible for the licenses of everything you download.

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

**Bench only; sends test sentences to that service.** `scripts/bench/run.py` and `google_ref.py` call Google Cloud Translation v2 (and, for the FLEURS run, Speech-to-Text v1, which receives the test audio) only when a key file exists and `--no-google` is not given. They are dev tools: the `tinglongwu` package never imports `scripts/`, and no normal `tlwu` / `tlwu-gui` / reverse-mode path calls them. `fetch_data.py` also downloads the test sets (FLORES-200, FLEURS) from the network.

**Google scores higher than every offline engine** in every direction we tested. The offline engines trade some quality for working with no network and no data leaving the machine. Small samples (50 sentences) are indicative only.

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

> UNTESTED: written on Windows without a Linux machine; only the command construction is unit-tested. Expect rough edges.

Reverse mode (you speak Chinese, Russian speech goes into Meet/Zoom) on a CPU-only Linux laptop, e.g. AMD Ryzen 7040:

```
sudo apt install pulseaudio-utils            # pactl (PipeWire: pipewire-pulse already provides it)
tlwu-setup --whisper small --pair zh-en --pair en-ru --pair ru-en --pair en-zh   # one-time, needs network
python -m tinglongwu.reverse.proto --list-mics
python -m tinglongwu.reverse.proto --mic "<your mic substring>" --compute cpu --mt opus     --tts-engine vosk --tts-python /path/to/tts-vosk/bin/python --tts-model /path/to/vosk-model-tts-ru-0.7-multi
```

- On Linux the program creates a virtual microphone automatically (`pactl load-module module-null-sink sink_name=tlwu_sink` + `module-remap-source ... source_name=tlwu_mic`), plays the Russian audio to `tlwu_sink`, and unloads both modules on exit. In Meet/Zoom choose the microphone `tlwu_mic`. `--no-vmic` disables this (then pass `--device <sink substring>`); `--list-speakers` lists output devices. Windows keeps VB-CABLE.
- Without CUDA, translation uses the OPUS/Argos pivot zh -> en -> ru (needs the four `--pair` models above); Whisper defaults to `small`. `--mt qwen` forces Qwen3 (GPU).
- The Russian voice model is **optional and opt-in** (no upstream license): no installer or default setup fetches it. Get it with `tlwu-setup --vosk-tts-ru` (or the menu prompt, y/N, default N, which first prints "Russian voice model license is not stated upstream; download at your own responsibility"). Without it, speaking Russian shows subtitles only and prints how to enable it.
- Speech uses fixed built-in voices on CPU: Vosk-TTS for Russian (`--tts-engine vosk`, voice ids 0-4 via `--speaker`) and Kokoro for English/Japanese (`--tts-engine kokoro`), each in its own venv. There is no reference-audio voice cloning.

## Acceptable Use / Consent
- Get the consent of every participant before recording, capturing or transcribing a call or meeting; recording and transcription laws vary by jurisdiction.
- Do not use this tool to circumvent DRM or other technical protection of content you have no right to copy.
- Do not use it to impersonate another person. Reverse mode speaks with fixed built-in voices only; there is no voice cloning, and you must not present synthesized speech as someone else's voice.

## Roadmap
Mic -> zh -> ru translation -> built-in-voice offline TTS (Vosk-TTS / Kokoro) -> virtual mic (VB-Cable / BlackHole / PipeWire null-sink), with echo exclusion so synthesized speech is not re-captured.

## Tests
`pip install -r requirements-dev.txt && pytest` (no models or network needed).

## Disclaimer
- **As is.** This software is provided "AS IS" under the MIT license, without warranty of any kind and without liability of the author for any damages (see [LICENSE](LICENSE)).
- **Research / educational demo.** It is a technology demonstration, not a certified or commercial product.
- **Output can be wrong.** Transcription and translation may contain errors. Do not rely on it for legal, medical, safety or financial decisions.
- **Your responsibility.** You must follow the laws that apply to you and get consent before recording or transcribing other people; see [Acceptable Use / Consent](#acceptable-use--consent).
- **Third-party components.** Models, libraries and drivers are under their own licenses. You install them from the upstream sources; this repository does not redistribute them (the only third-party binaries committed are the self-built llama.cpp Android AAR under `mobile/`, MIT, and the Windows exe, which bundles the runtime libraries listed in its `licenses/THIRD_PARTY_NOTICES.txt`). See [Licenses and sources](#licenses-and-sources).
- **No affiliation.** Not affiliated with or endorsed by Google, Microsoft, Apple, Zoom, Meet, Alibaba (Qwen), Tencent (Hunyuan / Hy-MT2), Meta, OpenAI, NVIDIA, VB-Audio, Existential Audio or any other third party named here. All trademarks belong to their owners.
- **Network guard.** The network guard is best-effort and is not a guarantee that no data leaves your machine.
- **Security.** See [SECURITY.md](SECURITY.md).
- **Privacy.** See [PRIVACY.md](PRIVACY.md) ([繁體中文](PRIVACY.zh-TW.md)).

MIT License. See [LICENSE](LICENSE). (c) 2026 Alonso.
