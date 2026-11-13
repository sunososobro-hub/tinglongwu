# tinglongWu mobile (聽攏有) - offline ru -> zh-Hant live subtitles (prototype v0)

Mic -> sherpa-onnx (GigaAM v3 ASR + Silero VAD) -> Qwen3-4B Q4_K_M (1.7B fallback) via llama.cpp (zh-Hant) -> full-screen subtitles.
No INTERNET permission, no model downloader. Models are side-loaded with adb.

## Build
    # one time: self-built llama.cpp AAR (needs NDK 28.2.13676358 + SDK cmake 3.22.1)
    powershell -File tool\build_llama_android.ps1   # Windows
    bash tool/build_llama_android.sh                 # Linux/macOS (ANDROID_HOME set)
    flutter pub get
    flutter test
    flutter build apk --release --target-platform android-arm64
    # -> build\app\outputs\flutter-apk\app-release.apk (debug-signed)

## CI build (no computer needed to get the APK)
`.github/workflows/mobile-apk.yml` builds the arm64 release APK on every push to `dev` that touches `mobile/**` (and on manual runs). It rebuilds the llama.cpp AAR from source with `tool/build_llama_android.sh` (Linux twin of the `.ps1`, same pinned commit/NDK/cmake; cached), runs `flutter analyze` + `flutter test`, checks the APK has no INTERNET permission, and replaces `tinglongwu-mobile-arm64.apk` on the rolling pre-release [`mobile-latest`](https://github.com/sunososobro-hub/tinglongwu/releases/tag/mobile-latest) (never marked Latest; `v*` releases are untouched). Pull requests only build.

### Signing (optional, owner-run once)
Without secrets each CI run signs the APK with a throwaway debug key (a signing key must not live in the Actions cache, which pull-request jobs can read), so every new preview needs an uninstall first, which also deletes imported models. With a release key, previews install over each other and keep the models. To sign with a real release key, run once on any computer with a JDK and `gh` (PowerShell; use the same password at both keytool prompts):

    keytool -genkeypair -v -keystore tlwu-release.jks -alias tlwu -keyalg RSA -keysize 4096 -validity 10000 -dname "CN=tinglongWu"
    $repo = 'sunososobro-hub/tinglongwu'
    gh secret set ANDROID_KEYSTORE_B64 -R $repo -b ([Convert]::ToBase64String([IO.File]::ReadAllBytes("$PWD\tlwu-release.jks")))
    gh secret set ANDROID_KEY_ALIAS -R $repo -b tlwu
    gh secret set ANDROID_KEYSTORE_PASSWORD -R $repo   # paste the password when asked
    gh secret set ANDROID_KEY_PASSWORD -R $repo        # same password

Keep `tlwu-release.jks` and the password backed up offline; losing them means users must uninstall to update. Switching from the debug key to the release key also needs one uninstall + reinstall (and a re-import of models).

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

**Privacy: transcripts are not logged by default.** The log sits on shared external storage (readable over adb and by apps with storage access), so recognized (ru) and translated (zh) text is replaced by `<redacted N chars>`; only timings and diagnostics are written. To debug with transcripts, build a debug APK with `--dart-define=TLWU_LOG_TRANSCRIPTS=true` (never ship or share such a build; delete `tlwu.log*` afterwards).

## Licenses
- tinglongWu: MIT (see ../LICENSE).
- GigaAM v3 speech model (ru ASR, ONNX export for sherpa-onnx): MIT.
- sherpa-onnx: Apache-2.0.
- `native/llama-cpp-dart-selfbuilt.aar` is llama.cpp (MIT) built from source; the upstream prebuilt AAR is not used. See "Native AAR" below.
- Qwen3 GGUF models: Apache-2.0. Silero VAD: MIT.
- `<MODELS_DIR>` above is wherever you keep the downloaded model files.

## Native AAR (`native/llama-cpp-dart-selfbuilt.aar`)
Built from source (locally by the script, and by CI on every APK build) from upstream llama.cpp commit `afeebe103bd99cda8f5dfaefcabadf890db7fda7` (the commit pinned by `llama_cpp_dart` 23dae0b) with the Android NDK 28.2.13676358 and SDK cmake 3.22.1: arm64-v8a only, Release, `GGML_CPU_ARM_ARCH=armv8.6-a+dotprod+i8mm+fp16`, OpenMP/llamafile/curl off. The script strips `libllama`, `libggml`, `libggml-base`, `libggml-cpu`, `libmtmd` and wraps them in an AAR with an empty `classes.jar`. The binary is committed for convenience and is not reproducible bit for bit (toolchain paths, timestamps). Android users who do not want to trust it can rebuild it themselves and overwrite the file (it prints the new SHA256):

    powershell -File tool\build_llama_android.ps1

The script fetches that exact commit from github.com/ggml-org/llama.cpp, so you can diff the sources you build against upstream.
