# tinglongWu mobile (聽攏有) - offline ru -> zh-Hant live subtitles (prototype v0)

Mic -> sherpa-onnx (GigaAM v3 ASR + Silero VAD) -> Qwen3-4B Q4_K_M (1.7B fallback) via llama.cpp (zh-Hant) -> full-screen subtitles.
No INTERNET permission, no model downloader. Models are side-loaded with adb.

## Build
    # one time: self-built llama.cpp AAR (needs NDK 28.2.13676358 + SDK cmake 3.22.1)
    powershell -File tool\build_llama_android.ps1
    flutter pub get
    flutter test
    flutter build apk --release --target-platform android-arm64
    # -> build\app\outputs\flutter-apk\app-release.apk (debug-signed)

## Install + side-load (PowerShell, phone on USB debugging)
    $pkg = 'com.tinglongwu.mobile'
    $dst = "/sdcard/Android/data/$pkg/files/models"
    adb install -r build\app\outputs\flutter-apk\app-release.apk
    adb shell pm grant $pkg android.permission.RECORD_AUDIO
    adb shell mkdir -p $dst
    adb push <MODELS_DIR>\gigaam3 $dst/
    adb push <MODELS_DIR>\Qwen3-4B-Q4_K_M.gguf $dst/
    adb shell am start -n $pkg/.MainActivity

Expected on device: models/gigaam3/{encoder.int8.onnx,decoder.onnx,joiner.onnx,tokens.txt}, models/silero_vad.onnx, models/Qwen3-4B-Q4_K_M.gguf (or Qwen3-1.7B-Q4_K_M.gguf).

## Logs
    adb pull /sdcard/Android/data/com.tinglongwu.mobile/files/logs/tlwu.log

Each translation logs latency (est. speech end -> zh shown), ASR-final -> zh, time to first token, tokens, tok/s, queue.

## Licenses
- tinglongWu: MIT (see ../LICENSE).
- GigaAM v3 speech model (ru ASR, ONNX export for sherpa-onnx): MIT.
- sherpa-onnx: Apache-2.0.
- `native/*.aar` is llama.cpp (MIT) built from source with `tool/build_llama_android.ps1`; the upstream prebuilt AAR is not used.
- Qwen3 GGUF models: Apache-2.0. Silero VAD: MIT.
- `<MODELS_DIR>` above is wherever you keep the downloaded model files.
