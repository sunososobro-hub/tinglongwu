# Models

Moved from the README (#166). All downloads go through `tlwu-setup`; see also [install](install.md).

## Models (one-time, needs network)
```
tlwu-setup --whisper small --langs ru --to zh
```
Translation uses Tencent Hy-MT2-1.8B (see below), direct source -> target. The former OPUS-MT route was removed in #471; `--engine opus` now exits with code 2.

Run `tlwu-setup --hymt` once; it covers every supported pair. If it is missing, tinglongWu stops and tells you the command.


**Japanese (`--lang ja`, auto candidates en,ru,ja,zh, `reverse.proto --to ja`)**: The former OPUS route had no usable Japanese, so every pair with Japanese is translated directly (no English pivot) by Tencent Hy-MT2-1.8B (Apache-2.0, Q4_K_M GGUF, ~1.1 GB): `tlwu-setup --hymt` plus `llama-cpp-python` (CPU wheel is fine). Without it, Japanese translation is unavailable (there is no fallback engine); speaking Japanese requires it. Japanese speech is Kokoro-82M v1.0 (Apache-2.0) in the same venv as the English voice; it additionally needs `misaki==0.9.4 pyopenjtalk-plus fugashi unidic-lite mojimoji jaconv addict regex` there (see `tinglongwu/reverse/tts_worker.py`).

**Default GPU engine: Hy-MT2** (`--engine auto` on a usable NVIDIA GPU, or `--engine hymt`): Tencent Hy-MT2-1.8B (llama.cpp, Q4_K_M GGUF, `tlwu-setup --hymt`, ~1.1 GB download) translates directly source -> target for zh/en/ru/ja/ko/de/fr/es/it/pt/vi/th/id (it/pt/vi/th/id added in #335; quality not yet measured, they stay Experimental). On our 200-sentence benchmark it matches the previous 8B LLM engine in quality (COMET x100: ru 86.5 vs 86.5, en 89.1 vs 88.8, ja 88.3 vs 87.8) at about 4x the speed (0.19 s vs 0.73 s per sentence) and ~1.5 GB VRAM instead of 5.1 GB, so Whisper turbo + Hy-MT2 fits a 4 GB card. `auto` needs ~2 GB of free VRAM after Whisper has loaded; if Hy-MT2 is not installed it stops and tells you to run `tlwu-setup --hymt` (no fallback engine, #276). With less free VRAM or on CPU, `auto` runs Hy-MT2 on the CPU (about 0.93 s per sentence on an i5-13500); `--engine opus` was removed in #471 and exits with code 2.

**Opt-in: Hy-MT2 7B** (#279, `tlwu-setup --hymt7b`, then `tlwu --engine hymt7b`): Tencent Hy-MT2-7B Q4_K_M GGUF (~4.8 GB) for GPUs with >= 8 GB VRAM; `auto` never picks it. Setup downloads the upstream `LICENSE.txt` first and refuses unless it is Apache-2.0 (the Hy-MT2 GitHub repo `LICENSE.txt` is Apache-2.0). Q4_K_M runs on stock llama.cpp; llama.cpp PR #22836 (STQ1_0) is only needed by the 1.25-bit GGUF. Not shipped as a default until it beats 1.8B by >= 2 XCOMET with MT p50 <= 0.8 s on an 8 GB GPU (owner test).

How to turn on Hy-MT2 7B (#1351; the default stays Hy-MT2 1.8B and nothing switches to 7B by itself):

1. Download it once: `tlwu-setup --hymt7b` (~4.8 GB; checks the Apache-2.0 license first).
2. Command line: add `--engine hymt7b`, e.g. `tlwu --lang ru --to zh --engine hymt7b`.
3. GUI: open **Advanced >**, set **Quality** to **Manual**, then pick **hymt7b** in **Engine**. Until the model is downloaded the entry reads `hymt7b (tlwu-setup --hymt7b)`. The choice is saved; set Quality back to Auto (or Engine to Auto) to return to the default.
4. To go back to the default from the command line, drop `--engine hymt7b` (or pass `--engine auto`).

When it helps: Russian as the source language is the weakest row for 1.8B. On the FLORES ru eval (200 sentences, [benchmarks](benchmarks.md)) 7B scores COMET 0.8765 ru->zh-Hant and 0.8759 ru->en vs 0.8696 / 0.8613 for 1.8B, but took about 4-5x as long per sentence on the CPU eval runners. Use it with a GPU with >= 8 GB VRAM; on the CPU tinglongWu prints a warning because lines arrive much later.

**Opt-in: your own local Ollama (`--engine ollama`) and Apple's on-device model (`--engine fm`, experimental)** (#356, contributed by jchodev): translate through a model you already run, with no translation-model download (Whisper is still needed for speech recognition). `auto` never picks either one, and the GUI default engine is unchanged on every platform. `ollama` sends each sentence to your local Ollama server (loopback addresses only, default `http://127.0.0.1:11434`) with thinking mode off; choose the model with `--ollama-model` / `TLWU_OLLAMA_MODEL` (without one, the first model already installed in Ollama is used) and the URL with `--ollama-url` / `TLWU_OLLAMA_URL`. tlwu never installs Ollama or pulls a model: install Ollama and run `ollama pull <model>` yourself. `fm` calls the `fm respond` command that ships with macOS 27 (Apple silicon, on-device model only, never Private Cloud Compute); run `sudo fm license` once. It is not yet tested by the maintainers and does nothing on other platforms. On a Mac, `scripts/make_mac_app.sh` builds a double-clickable launcher for the GUI (`~/Applications/TingLongWu.app`). Measured by the contributor on an M-series Mac: Ollama with a 9B model ~0.8-2.3 s per sentence, `fm` ~1.4 s alone but ~4 s alongside Whisper.

**Previous LLM engines**: removed (#276, #283, #308). Hy-MT2 is the translation LLM; their setup download flags no longer exist.


### Pinned model sources

`tinglongwu_setup/manifest.json` is committed: it pins every downloadable model to an exact Hugging Face commit and the SHA256 of each file. `tlwu-setup` downloads with `revision=<pinned commit>` and aborts on any hash mismatch; `tlwu-setup --verify` re-checks offline. Refresh pins with `scripts/make_model_pins.py` (maintainers; review the diff).

| Model | Source repo | Maintainer | Notes |
|---|---|---|---|
| Whisper tiny/base/small/medium/large-v3 (CT2) | `Systran/faster-whisper-*` | SYSTRAN | MIT |
| **Whisper large-v3-turbo (CT2)** | `mobiuslabsgmbh/faster-whisper-large-v3-turbo` | **mobiuslabsgmbh (third party, not OpenAI or SYSTRAN)** | MIT; a community CTranslate2 conversion, trust is limited to its pinned commit and hashes |

## Languages (moved from the README, #342)
Focus languages are Japanese, English and Russian (Chinese is the base). Every other language still works for Listen, but is **experimental**: less tested, and menus / the GUI label it so.

## Leftover OPUS code (kept on purpose)

`tinglongwu_setup/manifest.json` still pins the argos opus-ru-en / en-zh / ja-en packs and `tinglongwu_setup/downloader.py` still contains the OPUS download code. Nothing in the default install path uses them since #471; they are left in place for old installs and will be removed in a separate code issue (#1054 audit).
