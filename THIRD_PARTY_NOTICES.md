# Third-party notices: models and voices downloaded at runtime

`tlwu-setup` downloads the models below from their upstream sources. None is bundled in this repository.
The Apache-2.0 license text that applies to the Apache-2.0 rows is in [LICENSES/Apache-2.0.txt](LICENSES/Apache-2.0.txt)
(Apache-2.0 section 4(a)); MIT and CC-BY 4.0 rows link to their upstream license. Libraries and tools: [docs/licenses.md](docs/licenses.md).
Speaker model: [THIRD_PARTY.md](THIRD_PARTY.md).

| Model (id in code) | Source | License | Copyright |
|---|---|---|---|
| Whisper via faster-whisper, CT2 conversions: `Systran/faster-whisper-tiny`, `Systran/faster-whisper-base`, `Systran/faster-whisper-small`, `Systran/faster-whisper-medium`, `Systran/faster-whisper-large-v3` | https://huggingface.co/Systran | MIT | OpenAI (Whisper); SYSTRAN (conversion). See each repo's LICENSE |
| `mobiuslabsgmbh/faster-whisper-large-v3-turbo` | https://huggingface.co/mobiuslabsgmbh/faster-whisper-large-v3-turbo | MIT | OpenAI (Whisper); mobius labs GmbH (conversion). See the repo's LICENSE |
| `Qwen/Qwen3-8B-GGUF`, `Qwen/Qwen3-4B-GGUF` | https://huggingface.co/Qwen/Qwen3-4B-GGUF | Apache-2.0 | Alibaba Cloud / Qwen team. See the repo's LICENSE |
| `tencent/Hy-MT2-1.8B-GGUF` (Hy-MT2-1.8B) | https://huggingface.co/tencent/Hy-MT2-1.8B-GGUF , https://github.com/Tencent-Hunyuan/Hy-MT2 | Apache-2.0 (no territory or MAU limits) | Copyright (C) 2026 Tencent. All rights reserved. |
| OPUS-MT via Argos Translate: `opus-ru-en` (`https://argos-net.com/v1/translate-ru_en-1_9.argosmodel`), `opus-en-zh` (`https://argos-net.com/v1/translate-en_zh-1_9.argosmodel`), `opus-ja-en` (`https://argos-net.com/v1/translate-ja_en-1_1.argosmodel`) | https://github.com/Helsinki-NLP/Opus-MT , https://github.com/argosopentech/argospm-index | CC-BY 4.0 (attribution: Helsinki-NLP / OPUS-MT, Language Technology Research Group, University of Helsinki) | Helsinki-NLP |
| Kokoro-82M v1.0 ONNX (`kokoro-v1.0.int8.onnx`, `voices-v1.0.bin`) | https://github.com/thewh1teagle/kokoro-onnx/releases/tag/model-files-v1.0 | Apache-2.0 (as stated by kokoro-onnx) | hexgrad (Kokoro-82M). See upstream |
| Piper Russian voices `ru_RU-dmitri-medium`, `ru_RU-denis-medium` (`rhasspy/piper-voices`) | https://huggingface.co/rhasspy/piper-voices | CC0 datasets (OHF-Voice/voice-datasets); see each voice's MODEL_CARD | See each voice's MODEL_CARD |
| 3D-Speaker CAM++ `3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx` (optional `--speaker-model`) | https://github.com/k2-fsa/sherpa-onnx/releases/tag/speaker-recongition-models , https://github.com/modelscope/3D-Speaker | Apache-2.0 | Alibaba. See upstream |
| 3D-Speaker ERes2Net `3dspeaker_speech_eres2net_base_200k_sv_zh-cn_16k-common.onnx` (fallback, not downloaded by default) | same as above | Apache-2.0 | Alibaba. See upstream |
| Vosk-TTS Russian voice `vosk-model-tts-ru-0.7-multi` (`https://alphacephei.com/vosk/models/vosk-model-tts-ru-0.7-multi.zip`) | https://alphacephei.com/vosk/models , https://github.com/alphacep/vosk-tts | **unknown**: upstream states no license for the model (only the code is Apache-2.0) | unknown |

The Vosk-TTS Russian voice is opt-in: it is never downloaded by default, only with `tlwu-setup --vosk-tts-ru`. You are
responsible for its license.

Rows say "See upstream" where the upstream copyright line was not checked here; the linked repo's LICENSE is authoritative.
