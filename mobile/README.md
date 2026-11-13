# tinglongWu mobile (聽攏有) - offline ru -> zh-Hant live subtitles (prototype v0)

Mic -> sherpa-onnx (GigaAM v3 ASR + Silero VAD) -> Hy-MT2-1.8B Q4_K_M via llama.cpp (same translator and prompt as desktop) -> full-screen subtitles.
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
    adb push <MODELS_DIR>\silero_vad.onnx $dst/
    adb push <MODELS_DIR>\hymt2-1.8b\Hy-MT2-1.8B-Q4_K_M.gguf $dst/
    adb shell am start -n $pkg/.MainActivity

Expected on device: models/gigaam3/{encoder.int8.onnx,decoder.onnx,joiner.onnx,tokens.txt}, models/silero_vad.onnx, models/Hy-MT2-1.8B-Q4_K_M.gguf.

## Translation model
- File: `Hy-MT2-1.8B-Q4_K_M.gguf` (Tencent Hy-MT2-1.8B, Q4_K_M GGUF from the `tencent/Hy-MT2-1.8B-GGUF` Hugging Face repo), about 1.1 GB.
- License: Apache-2.0.
- Where to get it: the same file desktop `tlwu-setup --hymt` downloads to `models\hymt2-1.8b\`; the app never downloads it.
- Where to side-load it: `/sdcard/Android/data/com.tinglongwu.mobile/files/models/Hy-MT2-1.8B-Q4_K_M.gguf` (the `adb push` line above).
- Output may be Simplified Chinese (the model-card prompt asks for 中文; desktop converts with OpenCC, the phone has no converter yet); such lines are flagged NOT-TRAD in the log.

## Logs
    adb pull /sdcard/Android/data/com.tinglongwu.mobile/files/logs/tlwu.log

Each translation logs latency (est. speech end -> zh shown), ASR-final -> zh, time to first token, tokens, tok/s, queue.

**Privacy: transcripts are not logged by default.** The log sits on shared external storage (readable over adb and by apps with storage access), so recognized (ru) and translated (zh) text is replaced by `<redacted N chars>`; only timings and diagnostics are written. To debug with transcripts, build a debug APK with `--dart-define=TLWU_LOG_TRANSCRIPTS=true` (never ship or share such a build; delete `tlwu.log*` afterwards).

## Licenses
- tinglongWu: MIT (see ../LICENSE).
- GigaAM v3 speech model (ru ASR, ONNX export for sherpa-onnx): MIT.
- sherpa-onnx: Apache-2.0.
- `native/llama-cpp-dart-selfbuilt.aar` is llama.cpp (MIT) built from source; the upstream prebuilt AAR is not used. See "Native AAR" below.
- Hy-MT2-1.8B GGUF model: Apache-2.0. Silero VAD: MIT.
- `<MODELS_DIR>` above is wherever you keep the downloaded model files.

## Native AAR (`native/llama-cpp-dart-selfbuilt.aar`)
Built locally (not by CI) from upstream llama.cpp commit `afeebe103bd99cda8f5dfaefcabadf890db7fda7` (the commit pinned by `llama_cpp_dart` 23dae0b) with the Android NDK 28.2.13676358 and SDK cmake 3.22.1: arm64-v8a only, Release, `GGML_CPU_ARM_ARCH=armv8.6-a+dotprod+i8mm+fp16`, OpenMP/llamafile/curl off. The script strips `libllama`, `libggml`, `libggml-base`, `libggml-cpu`, `libmtmd` and wraps them in an AAR with an empty `classes.jar`. The binary is committed for convenience and is not reproducible bit for bit (toolchain paths, timestamps). Android users who do not want to trust it can rebuild it themselves and overwrite the file (it prints the new SHA256):

    powershell -File tooluild_llama_android.ps1

The script fetches that exact commit from github.com/ggml-org/llama.cpp, so you can diff the sources you build against upstream.
