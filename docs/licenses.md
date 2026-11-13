# Licenses and sources
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
| llama-cpp-python (runs Hy-MT2) | MIT | github.com/abetlen/llama-cpp-python |
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
| jieba, pypinyin, pypinyin-dict, cn2an, ordered-set (misaki[zh] extras for the Chinese voice, `reverse.proto --from en`; Kokoro venv only; you install them) | MIT (all, from PyPI metadata) | pypi.org/project/<name> |
| GigaAM v3 (Russian ASR, mobile app only; ONNX export by k2-fsa/sherpa-onnx, you side-load it) | MIT (GigaAM repo LICENSE and the ai-sage/GigaAM-v3 model card; the sherpa-onnx export card points to the same GigaAM LICENSE) | github.com/salute-developers/GigaAM, huggingface.co/ai-sage/GigaAM-v3 |
| sherpa-onnx (mobile app only) | Apache-2.0 | github.com/k2-fsa/sherpa-onnx |
| llama.cpp (self-built AAR in `mobile/`) | MIT (per mobile/README) | github.com/ggml-org/llama.cpp |
| BlackHole (macOS virtual audio device) | GPL-3.0; Existential Audio requires a separate license for non-GPL-3.0 projects | github.com/ExistentialAudio/BlackHole. **You install it (`mac_install.sh` calls Homebrew on your machine); this project does not bundle or redistribute it.** |
| VB-CABLE (Windows virtual audio cable) | Donationware, proprietary (VB-Audio); redistribution needs a license from VB-Audio | vb-audio.com/Cable. **You install it; this project does not bundle or redistribute it.** |

Verify upstream licenses yourself before redistributing models.

Notes on the table: (1) No GPL component is bundled in this repository or in the Windows exe (the exe build excludes reverse mode, so Kokoro, espeak-ng and phonemizer are not in it). The GPL pieces (espeak-ng, phonemizer, pulled in by kokoro-onnx) are only installed by you with pip into a separate Kokoro venv and are used by a separate worker process, never imported by the main program. (2) Nothing shipped or downloaded by `tlwu-setup` is non-commercial; NLLB-200 (CC-BY-NC) is the only NC model mentioned and it is not shipped. (3) The Vosk-TTS Russian voice model has no stated license upstream: treat it as unknown. This is a non-commercial research project and you are responsible for the licenses of everything you download.
