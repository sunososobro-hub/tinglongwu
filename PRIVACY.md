# Privacy statement

[繁體中文](PRIVACY.zh-TW.md)

tinglongWu translates speech on your own computer. This page says what stays local and every case where the network is used. It describes the code in this repository; it is not a legal guarantee (see "Limits").

## What stays on your machine
- **Audio and text are processed locally and are not uploaded.** Audio lives in memory only.
- **Transcripts are not saved by default.** Only the opt-in `--save` (GUI: the save checkbox) and `--save-result PATH` (speak mode) write text to disk, as local files (`transcripts/`, JSONL).
- **Local files tlwu writes:** `gui.json` (your settings), the user config file `config` in your config folder (`auto_update`, `ui_lang`), run logs and `install.log` (installer self-test) in your log folder, `speedprobe.json` (speed-test result), `netguard.jsonl` (see below), a temp file holding only a language code, and the opt-in transcripts above.
- **Models** are stored in the `models/` folder of the install (or `TLWU_MODELS` / `--models-dir`); they are only downloaded, never uploaded.
- **No telemetry.** None was found in the 2026-11 audit. On import, tlwu also sets `HF_HUB_OFFLINE`, `TRANSFORMERS_OFFLINE`, `HF_HUB_DISABLE_TELEMETRY`, `DO_NOT_TRACK` and similar variables.

## Every network access
The runtime package `tinglongwu/` contains no network code; a Python-level network guard also blocks non-loopback connections while tlwu runs. Network-capable code lives in `tinglongwu_setup/` and the install/update scripts:

| # | What | When | What is sent | How to turn it off |
|---|------|------|--------------|--------------------|
| 1 | Model download by `tlwu-setup`: Hugging Face (Whisper, Qwen3 GGUF, Hy-MT2 GGUF; pinned commit + SHA256), Argos OPUS index on `raw.githubusercontent.com` and archives from argos-net.com (only `ru-en` and `en-zh` are pinned; other pairs are not) | Only when you run setup and answer yes | Ordinary HTTPS requests (your IP, user agent, the requested file) | Do not run setup / answer N |
| 2 | Kokoro TTS model download in `scripts/mac_install.sh` (third-party GitHub release; size check only, no hash). Vosk Russian TTS model: manual download from alphacephei.com, no hash | macOS install / manual | Ordinary HTTPS requests | Skip the step |
| 3 | Python dependencies: PyPI, plus a third-party index for the llama-cpp wheel (`requirements-llama-cuda.txt`) | `install.ps1`, `tlwu-setup --deps`, `mac_install.sh`, auto-update when requirements change | Ordinary HTTPS requests | Do not run the installers |
| 4 | Opt-in auto-update: `git fetch` of the `main` branch on GitHub (6 s cap); may run `pip install -e .` if requirements changed | Asked once, default off | Git protocol requests | Answer no, or set `TLWU_NO_UPDATE=1` |
| 5 | Installers fetching the source: Windows setup wizard `tinglongwu-setup.exe` (bundles the source; `install.ps1` then downloads Python 3.12 from python.org if missing, SHA256-checked, and the Python packages); Windows `scripts/bootstrap_windows.ps1` (git clone from GitHub, or the GitHub zip of the tag when git is missing); Linux `scripts/install.sh` (`curl \| bash` from the GitHub release, then git clone); macOS `mac_install.sh` (`curl \| bash` from the GitHub release, git clone, Homebrew) | When you run it | Ordinary HTTPS requests | Do not run it |
| 6 | Network-guard report (`tinglongwu/netreport.py`): opens your browser on a pre-filled GitHub issue | Only if a connection was blocked and you answer y; the exact text is shown first and you submit it yourself | Version, commit, OS string, host/port/package/mode/count, up to 12 `module:function:line` stack frames. No transcript, username, home path or hostname | Answer N |
| 6b | Report a problem (GUI button / `tlwu --report`, `tinglongwu/report.py`): opens your browser on a pre-filled GitHub issue | Only when you click it; you review the text on GitHub and submit it yourself | App version, OS, Python, CPU model/AVX, cores, RAM, GPU/VRAM, NVIDIA driver, package versions, current settings (GUI) or language pair/engine (CLI), the last install self-test block, up to the last 200 run-log lines (cut to fit the URL); home path and user name are redacted | Do not click it |
| 7 | Mobile: the release manifest has no INTERNET permission (the debug manifest does) | - | - | - |
| 8 | Developer-only scripts (`scripts/bench/*`, `scripts/make_lock.py`), not shipped | - | - | - |

`TLWU_ALLOW_NET=1` disables the network guard; do not set it unless you need to.

`netguard.jsonl` (in your user log directory) records blocked attempts: time, host, port, package, trimmed stack. It has no PII and is never sent automatically.

## Payment
Paid versions: checkout (email, license key, card data) is handled entirely by the payment provider. tlwu does not store payment data.

## Limits
- The network guard blocks Python-level sockets only. Native libraries (ctranslate2, onnxruntime, llama.cpp) and other subprocesses are not covered; an OS firewall test is the independent check (see README, "Privacy model").
- Some downloads are not yet hash-pinned (rows 1-2).
- Your OS may write crash dumps or a pagefile that can contain memory contents; tlwu cannot control this.

## Contact
Privacy questions: open an issue at https://github.com/sunososobro-hub/tinglongwu/issues, or report sensitive matters privately as described in [SECURITY.md](SECURITY.md).
