# Third-party notices: models and voices downloaded at runtime

tinglongWu does not ship any model. `tlwu-setup` downloads the models below from their upstream sources.
Each model stays under its own license. Library licenses are in [docs/licenses.md](docs/licenses.md);
speaker-diarization components are in [THIRD_PARTY.md](THIRD_PARTY.md).

| Model | Source | License | Copyright |
|---|---|---|---|
| Whisper tiny / base / small / medium / large-v3 (CT2): `Systran/faster-whisper-tiny`, `Systran/faster-whisper-base`, `Systran/faster-whisper-small`, `Systran/faster-whisper-medium`, `Systran/faster-whisper-large-v3` | https://huggingface.co/Systran | MIT | OpenAI (Whisper); Systran (CT2 conversion) |
| Whisper large-v3-turbo (CT2): `mobiuslabsgmbh/faster-whisper-large-v3-turbo` | https://huggingface.co/mobiuslabsgmbh/faster-whisper-large-v3-turbo | MIT | OpenAI (Whisper); mobiuslabs GmbH (CT2 conversion) |
| Hy-MT2 1.8B GGUF: `tencent/Hy-MT2-1.8B-GGUF` (default translation engine) | https://huggingface.co/tencent/Hy-MT2-1.8B-GGUF , https://github.com/Tencent-Hunyuan/Hy-MT2 | Apache-2.0 | Copyright (C) 2026 Tencent. All rights reserved. |
| Qwen3 GGUF: `Qwen/Qwen3-8B-GGUF`, `Qwen/Qwen3-4B-GGUF` (legacy, being removed) | https://huggingface.co/Qwen | Apache-2.0 | Alibaba Cloud / Qwen team |
| OPUS-MT packs: `opus-ru-en`, `opus-en-zh`, `opus-ja-en` (Helsinki-NLP, via the Argos index) | https://github.com/Helsinki-NLP/Opus-MT , https://github.com/argosopentech/argospm-index | CC-BY 4.0 (attribution required) | Helsinki-NLP / University of Helsinki |
| Piper Russian voices `ru_RU-dmitri-medium`, `ru_RU-denis-medium`: `rhasspy/piper-voices` | https://huggingface.co/rhasspy/piper-voices | CC0 datasets only (OHF-Voice/voice-datasets); Piper code MIT | see each voice's MODEL_CARD |
| Kokoro v1.0: `kokoro-v1.0.int8.onnx` and `voices-v1.0.bin` (English/Japanese voice) | https://github.com/thewh1teagle/kokoro-onnx/releases/tag/model-files-v1.0 | Apache-2.0 (as stated by kokoro-onnx) | Kokoro-82M authors (hexgrad) |
| 3D-Speaker CAM++ `3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx` (opt-in `--speaker-model`) | https://github.com/k2-fsa/sherpa-onnx/releases/tag/speaker-recongition-models | Apache-2.0 (3D-Speaker project) | Alibaba |
| Vosk-TTS Russian voice `vosk-model-tts-ru-0.7-multi` (opt-in only: `tlwu-setup --vosk-tts-ru`; never downloaded by default) | https://alphacephei.com/vosk/models | **unknown** (upstream states no model license) | unknown |

## Apache-2.0 license text

Models under Apache-2.0 are covered by Apache License 2.0, https://www.apache.org/licenses/LICENSE-2.0.
For Hy-MT2 the upstream `LICENSE.txt` (plain Apache-2.0, no extra terms) is downloaded next to the model
by `tlwu-setup --hymt`, so every recipient gets a copy of the license (Apache-2.0 section 4(a)).
