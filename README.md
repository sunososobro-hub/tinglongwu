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

## One-command install (Windows / Mac / Linux)

Each command installs the exact version of the latest GitHub Release (`main` is stable; `dev` is for development). Re-running is safe.

| OS | Command |
|---|---|
| Windows (PowerShell) | `irm https://github.com/sunososobro-hub/tinglongwu/releases/latest/download/install.ps1 \| iex` |
| macOS (Terminal) | `curl -fsSL https://github.com/sunososobro-hub/tinglongwu/releases/latest/download/mac_install.sh \| bash` |
| Linux (bash) | `curl -fsSL https://github.com/sunososobro-hub/tinglongwu/releases/latest/download/install.sh \| bash` |

Each release also lists `SHA256SUMS.txt`; to check a script before running it, download it and compare. The Linux installer asks y/N before any `apt` step and downloads no models (the menu asks before every download). The Windows command downloads the source to `%USERPROFILE%\tinglongwu` and runs the same `install.ps1` as below (the execution-policy bypass is for that one child process only).

Windows ZIP install (`install.bat`) and the menu keys, Mac details, source install, GPU: [docs/install.md](docs/install.md). The Windows release zip is unsigned and not reproducible: [read this first](docs/install.md#releases-read-before-trusting-a-binary).

## System requirements

| Platform | Status |
|---|---|
| Windows/Linux + NVIDIA GPU | primary |
| Windows/Linux CPU only: 2020+ AVX2, 8+ threads, 16GB | supported |
| Apple Silicon (M1+) | supported |
| Intel Mac, pre-2019 CPU, no AVX2 | not supported (`--force`) |

## Usage
```
tlwu-menu                     # menu: Meeting / Speak / Listen (Windows: desktop shortcut; Linux: bash ~/tinglongwu/scripts/linux_menu.sh)
tlwu --lang ru                # live system audio -> Traditional Chinese subtitles
tlwu --file talk.mp3 --to en  # translate a file
tlwu-gui                      # floating subtitle window
```
All options, streaming drafts, sentence merging, GUI, glossary, known limitations: [docs/usage.md](docs/usage.md).

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

Windows verified so far: one-line install and Listen; the rest is pending (linked issues).

## More docs
- [Privacy model](docs/privacy.md): `tlwu-setup` is the only networked program; `tlwu` runs with a network guard (defence in depth, not proof); firewall block test.
- [Models](docs/models.md) · [Install / GPU / releases](docs/install.md) · [Benchmarks](docs/benchmarks.md) · [Licenses](docs/licenses.md) · [Linux reverse mode (experimental)](docs/linux-reverse.md)

## Acceptable Use / Consent
- Get the consent of every participant before recording, capturing or transcribing a call or meeting; recording and transcription laws vary by jurisdiction.
- Do not use this tool to circumvent DRM or other technical protection of content you have no right to copy.
- Do not use it to impersonate another person. Reverse mode speaks with fixed built-in voices only; there is no voice cloning, and you must not present synthesized speech as someone else's voice.

## Tests
`pip install -r requirements-dev.txt && pytest` (no models or network needed).

## Disclaimer
Provided as is (MIT, no warranty); output can be wrong; you are responsible for consent and local law. Full text: [docs/disclaimer.md](docs/disclaimer.md). Security: [SECURITY.md](SECURITY.md). Privacy: [PRIVACY.md](PRIVACY.md) ([繁體中文](PRIVACY.zh-TW.md)).

MIT License. See [LICENSE](LICENSE). (c) 2026 Alonso.
