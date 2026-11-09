# tinglongWu mobile (聽攏有) - offline ru -> zh-Hant live subtitles (prototype v0)

Mic -> sherpa-onnx streaming zipformer ru (partials) -> Qwen3-1.7B Q4_K_M via llama.cpp (zh-Hant) -> full-screen subtitles.
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
    adb push C:\Users\user\tlwu-eval\models\mobile\sherpa-ru $dst/
    adb push C:\Users\user\tlwu-eval\models\mobile\Qwen3-1.7B-Q4_K_M.gguf $dst/
    adb shell am start -n $pkg/.MainActivity

Expected on device: models/sherpa-ru/{encoder.int8.onnx,decoder.onnx,joiner.int8.onnx,tokens.txt}, models/Qwen3-1.7B-Q4_K_M.gguf.

## Logs
    adb pull /sdcard/Android/data/com.tinglongwu.mobile/files/logs/tlwu.log

Each translation logs latency (est. speech end -> zh shown), ASR-final -> zh, time to first token, tokens, tok/s, queue.
