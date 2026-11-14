# tinglongWu mobile (聽攏有) - offline zh/en/ja/ko/yue -> zh-Hant live subtitles (prototype v0)

Mic -> sherpa-onnx (SenseVoice-small ASR (zh/en/ja/ko/yue auto-detect) + Silero VAD) -> Hy-MT2-1.8B Q4_K_M via llama.cpp (same translator and prompt as desktop) -> full-screen subtitles.
No INTERNET permission and no built-in downloader: you download the models with the phone's browser and import them in the app (or side-load them with adb).

## Phone only (no computer)
1. On the phone, open https://github.com/sunososobro-hub/tinglongwu/releases/tag/mobile-latest and download `tinglongwu-mobile-arm64.apk`.
2. Open the downloaded APK and install it (Android asks once to allow installs from your browser). Needs an arm64 phone (almost every phone since 2017).
3. From the same page, download `tlwu-mobile-models.zip` (about 1.4 GB: SenseVoice-small ASR, Silero VAD, Hy-MT2-1.8B translator).
4. Open the app, tap **匯入模型 / Import models** -> **Pick files**, choose the zip from Downloads (single `.onnx` / `.gguf` / `.txt` model files work too). Every file is checked against `assets/models_manifest.json` (name, size, SHA-256) and copied into the app's `files/models`; anything that does not match is rejected. Then tap **Done, start** and allow the microphone.
5. You can delete the downloaded zip afterwards. Updating the app: download the new APK from the same page and install over the old one (models stay). If Android says the package conflicts, that preview was signed with a throwaway key (no release key configured yet, see "Signing"): uninstall, install, and import the models again (uninstalling deletes them).

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

## Google Play (AAB + model asset packs)
The `mobile-apk` workflow also builds `app-release.aab` for Google Play. The models do not fit in the
200 MB base, so they ride in two on-demand Play Asset Delivery packs: `models_asr` (SenseVoice + Silero
VAD) and `models_llm` (Hy-MT2 GGUF). CI fills them from the same official sources as the models zip
(`tool/fill_asset_packs.py`, size + SHA-256 checked against the manifest); in git the pack folders are
empty. The Play Store does the download, so the app still has no INTERNET permission. When the app was
installed from Play, the Import screen shows **從 Play 下載模型 / Get models**: the packs are fetched,
every file goes through the same checked import, then the packs are removed to free the space. A
side-loaded APK does not show the button and keeps the manual zip import.

Owner steps, once:
1. Release key secrets as in "Signing" above (that key becomes the Play **upload key**; Play App
   Signing keeps the app signing key, so Play builds and GitHub preview APKs have different signers and
   cannot update each other).
2. Play Console: create the app with package `com.tinglongwu.mobile`, then upload the first AAB by hand
   (artifact `mobile-aab` of a `mobile-apk` run on `dev`) to **Internal testing**. The Play API cannot
   create an app.
3. Google Cloud: create a service account, add a JSON key; Play Console > Users and permissions: invite
   the service account email with release rights for this app. Then
   `gh secret set PLAY_SA_JSON -R sunososobro-hub/tinglongwu < key.json`.
4. After that every push to `dev` under `mobile/` uploads to the internal track as a draft. Once the
   app has had its first release, `gh variable set PLAY_RELEASE_STATUS -R sunososobro-hub/tinglongwu -b completed`
   makes the uploads go out to internal testers directly.

## Models bundle (`tlwu-mobile-models.zip`)
Every model allows redistribution (SenseVoice FunASR model license, Silero VAD MIT, Hy-MT2-1.8B Apache-2.0); the zip carries their license texts and a `NOTICE.txt`. All files are unmodified upstream files: `tool/fetch_models.sh` downloads them from the official sources (k2-fsa/sherpa-onnx `asr-models` release, `tencent/Hy-MT2-1.8B-GGUF`), and `tool/make_models_zip.py` checks each against `assets/models_manifest.json` (size + SHA-256) before zipping. The `mobile-apk` workflow rebuilds and re-uploads the zip on `mobile-latest` only when the manifest changes (the manifest hash is kept in a hidden marker in the release notes; SHA-256 values are in the notes' Details block). If a model changes, update the manifest and `fetch_models.sh` in the same PR.

    bash tool/fetch_models.sh <DIR>      # -> <DIR>/models, <DIR>/licenses
    python3 -I tool/make_models_zip.py <DIR>/models <DIR>/licenses tlwu-mobile-models.zip

## Install + side-load with adb (PowerShell, phone on USB debugging)
    $pkg = 'com.tinglongwu.mobile'
    $dst = "/sdcard/Android/data/$pkg/files/models"
    adb install -r build\app\outputs\flutter-apk\app-release.apk
    adb shell pm grant $pkg android.permission.RECORD_AUDIO
    adb shell mkdir -p $dst
    adb push <MODELS_DIR>\sensevoice $dst/
    adb push <MODELS_DIR>\silero_vad.onnx $dst/
    adb push <MODELS_DIR>\hymt2-1.8b\Hy-MT2-1.8B-Q4_K_M.gguf $dst/
    adb shell am start -n $pkg/.MainActivity

Expected on device: models/sensevoice/{model.int8.onnx,tokens.txt}, models/silero_vad.onnx, models/Hy-MT2-1.8B-Q4_K_M.gguf.

## Translation model
- File: `Hy-MT2-1.8B-Q4_K_M.gguf` (Tencent Hy-MT2-1.8B, Q4_K_M GGUF from the `tencent/Hy-MT2-1.8B-GGUF` Hugging Face repo), about 1.1 GB.
- License: Apache-2.0.
- Where to get it: the same file desktop `tlwu-setup --hymt` downloads to `models\hymt2-1.8b\`; the app never downloads it.
- Where to side-load it: `/sdcard/Android/data/com.tinglongwu.mobile/files/models/Hy-MT2-1.8B-Q4_K_M.gguf` (the `adb push` line above).
- The model-card prompt asks for 中文, so output may be Simplified; like desktop (OpenCC s2twp), the app converts it to Traditional Chinese with Taiwan wording (`lib/logic/s2tw.dart`, pure Dart, tables generated from OpenCC data by `tool/gen_s2tw_data.py`). Lines that still contain Simplified-only characters are flagged NOT-TRAD in the log.

## Logs
    adb pull /sdcard/Android/data/com.tinglongwu.mobile/files/logs/tlwu.log

Each translation logs latency (est. speech end -> zh shown), ASR-final -> zh, time to first token, tokens, tok/s, queue.

**Privacy: transcripts are not logged by default.** The log sits on shared external storage (readable over adb and by apps with storage access), so recognized (source-language) and translated (zh-Hant) text is replaced by `<redacted N chars>`; only timings and diagnostics are written. To debug with transcripts, build a debug APK with `--dart-define=TLWU_LOG_TRANSCRIPTS=true` (never ship or share such a build; delete `tlwu.log*` afterwards).

## Licenses
- tinglongWu: MIT (see ../LICENSE).
- SenseVoice-small speech model (zh/en/ja/ko/yue ASR, int8 ONNX export for sherpa-onnx): FunASR Model Open Source License (see LICENSES/SenseVoice-LICENSE.txt in the zip).
- sherpa-onnx: Apache-2.0.
- `native/llama-cpp-dart-selfbuilt.aar` is llama.cpp (MIT) built from source; the upstream prebuilt AAR is not used. See "Native AAR" below.
- Hy-MT2-1.8B GGUF model: Apache-2.0. Silero VAD: MIT.
- `lib/logic/s2tw_data.dart`: generated from the OpenCC dictionaries (Copyright BYVoid and contributors, Apache-2.0, https://github.com/BYVoid/OpenCC), same data as the desktop `opencc-python-reimplemented` package.
- `<MODELS_DIR>` above is wherever you keep the downloaded model files.

## Native AAR (`native/llama-cpp-dart-selfbuilt.aar`)
Built from source (locally by the script, and by CI on every APK build) from upstream llama.cpp commit `afeebe103bd99cda8f5dfaefcabadf890db7fda7` (the commit pinned by `llama_cpp_dart` 23dae0b) with the Android NDK 28.2.13676358 and SDK cmake 3.22.1: arm64-v8a only, Release, `GGML_CPU_ARM_ARCH=armv8.6-a+dotprod+i8mm+fp16`, OpenMP/llamafile/curl off. The script strips `libllama`, `libggml`, `libggml-base`, `libggml-cpu`, `libmtmd` and wraps them in an AAR with an empty `classes.jar`. The binary is committed for convenience and is not reproducible bit for bit (toolchain paths, timestamps). Android users who do not want to trust it can rebuild it themselves and overwrite the file (it prints the new SHA256):

    powershell -File tool\build_llama_android.ps1

The script fetches that exact commit from github.com/ggml-org/llama.cpp, so you can diff the sources you build against upstream.
