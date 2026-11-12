# Models

Moved from the README (#166). All downloads go through `tlwu-setup`; see also [install](install.md).

## Models (one-time, needs network)
```
tlwu-setup --whisper small --langs ru --to zh
```
Translation uses OPUS-MT models as packaged by Argos Translate, run directly on CTranslate2 (no Argos/torch install). Route: source -> English -> Chinese; the English step is skipped when the source is English.

Any language pair with an OPUS-MT/Argos package works: `--langs` fetches source->English, `--to` fetches English->target (e.g. `--langs ru,ja --to zh,ko`). Fetch only what you need. If a pair is missing, tinglongWu stops and names the pair and the exact download command.


**Japanese (`--lang ja`, auto candidates en,ru,ja,zh, `reverse.proto --to ja`)**: OPUS has no usable Japanese, so every pair with Japanese is translated directly (no English pivot) by Tencent Hy-MT2-1.8B (Apache-2.0, Q4_K_M GGUF, ~1.1 GB): `tlwu-setup --hymt` plus `llama-cpp-python` (CPU wheel is fine). Without it, listening falls back to the OPUS ja->en->zh pivot (poor); speaking Japanese requires it. Japanese speech is Kokoro-82M v1.0 (Apache-2.0) in the same venv as the English voice; it additionally needs `misaki==0.9.4 pyopenjtalk-plus fugashi unidic-lite mojimoji jaconv addict regex` there (see `tinglongwu/reverse/tts_worker.py`).

**Default GPU engine: Hy-MT2** (`--engine auto` on a usable NVIDIA GPU, or `--engine hymt`): Tencent Hy-MT2-1.8B (llama.cpp, Q4_K_M GGUF, `tlwu-setup --hymt`, ~1.1 GB download) translates directly source -> target for zh/en/ru/ja/ko/de/fr/es. On our 200-sentence benchmark it matches Qwen3-8B in quality (COMET x100: ru 86.5 vs 86.5, en 89.1 vs 88.8, ja 88.3 vs 87.8) at about 4x the speed (0.19 s vs 0.73 s per sentence) and ~1.5 GB VRAM instead of 5.1 GB, so Whisper turbo + Hy-MT2 fits a 4 GB card. `auto` needs ~2 GB of free VRAM after Whisper has loaded; if Hy-MT2 is not installed it falls back to the Qwen tiers below, then OPUS. On CPU the default stays OPUS.

**Opt-in: Hy-MT2 7B** (#279, `tlwu-setup --hymt7b`, then `tlwu --engine hymt7b`): Tencent Hy-MT2-7B Q4_K_M GGUF (~4.8 GB) for GPUs with >= 8 GB VRAM; `auto` never picks it. Setup downloads the upstream `LICENSE.txt` first and refuses unless it is Apache-2.0 (the Hy-MT2 GitHub repo `LICENSE.txt` is Apache-2.0). Q4_K_M runs on stock llama.cpp; llama.cpp PR #22836 (STQ1_0) is only needed by the 1.25-bit GGUF. Not shipped as a default until it beats 1.8B by >= 2 XCOMET with MT p50 <= 0.8 s on an 8 GB GPU (owner test).

**Qwen LLM engine (optional, `--engine qwen` / `qwen4b`; used by `auto` only when Hy-MT2 is not installed)**: a local Qwen3 model (llama.cpp, Q4_K_M GGUF) translates directly source -> Chinese (no English pivot, thinking mode off, repetition guarded), then OpenCC converts per `--zh-variant`. It is much more accurate than OPUS and does not produce garbage on very short lines. `tlwu-setup --qwen` downloads Qwen3-8B and Qwen3-4B (~7.5 GB) into the models dir; it also needs `llama-cpp-python` (CUDA build). `auto` checks free VRAM after Whisper has loaded: Qwen3-8B if >= 5.8 GB is free, else Qwen3-4B if >= 3.5 GB, else OPUS (also on CPU or if Qwen fails to load); one line at startup says which engine was chosen. Force one with `--engine qwen` (8B), `qwen4b`, `opus` or `m2m`.


### Pinned model sources

`tinglongwu_setup/manifest.json` is committed: it pins every downloadable model to an exact Hugging Face commit and the SHA256 of each file. `tlwu-setup` downloads with `revision=<pinned commit>` and aborts on any hash mismatch; `tlwu-setup --verify` re-checks offline. Refresh pins with `scripts/make_model_pins.py` (maintainers; review the diff).

| Model | Source repo | Maintainer | Notes |
|---|---|---|---|
| Whisper tiny/base/small/medium/large-v3 (CT2) | `Systran/faster-whisper-*` | SYSTRAN | MIT |
| **Whisper large-v3-turbo (CT2)** | `mobiuslabsgmbh/faster-whisper-large-v3-turbo` | **mobiuslabsgmbh (third party, not OpenAI or SYSTRAN)** | MIT; a community CTranslate2 conversion, trust is limited to its pinned commit and hashes |
| Qwen3-8B / 4B Q4_K_M GGUF | `Qwen/Qwen3-*-GGUF` | Qwen (Alibaba) | Apache-2.0 |
| OPUS-MT (Argos packages) | `argos-net.com` | Argos Open Tech | CC-BY 4.0; `ru-en` and `en-zh` are pinned by URL + archive SHA256; other pairs are looked up in the mutable Argos index on first use and only pinned afterwards (a warning is printed) |

## Languages (moved from the README, #342)
Focus languages are Japanese, English and Russian (Chinese is the base). Every other language still works for Listen, but is **experimental**: less tested, and menus / the GUI label it so.
