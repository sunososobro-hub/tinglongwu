# tinglongWu mobile (聽攏有) - offline zh/en/ja/ko/yue -> zh-Hant live subtitles (prototype v0)

Mic -> sherpa-onnx (SenseVoice-small ASR (zh/en/ja/ko/yue auto-detect) + Silero VAD) -> Hy-MT2-1.8B Q4_K_M via llama.cpp (same translator and prompt as desktop) -> full-screen subtitles.
No INTERNET permission and no built-in downloader: you download the models with the phone's browser and import them in the app (or side-load them with adb).

## Phone only (no computer)
1. On the phone, open https://github.com/sunososobro-hub/tinglongwu/releases/tag/mobile-latest and download `tinglongwu-mobile-arm64.apk`.
2. Open the downloaded APK and install it (Android asks once to allow installs from your browser). Needs an arm64 phone (almost every phone since 2017).
3. From the same page, download `tlwu-mobile-models.zip` (about 1.4 GB: SenseVoice-small ASR, Silero VAD, Hy-MT2-1.8B translator).
4. Open the app, tap **匯入模型 / Import models** -> **Pick files**, choose the zip from Downloads (single `.onnx` / `.gguf` / `.txt` model files work too). Every file is checked against `assets/models_manifest.json` (name, size, SHA-256) and copied into the app's `files/models`; anything that does not match is rejected. Then tap **Done, start** and allow the microphone.
5. Optional, for 對話模式 (conversation mode, #558): import the packs you need the same way (**Import models** -> **Pick files**, one or several zips at once). Each zip is read by its name and every file is checked against `assets/voice_packs.json` (size, SHA-256); without a pack, conversation mode shows text only. Shortcut in the app: in 對話翻譯 tap **下載語音包 (N MB)** next to the language pill (or in the language sheet); it lists only the packs the chosen 對方語言 is missing, opens each zip in the browser and imports the downloaded file.
   - `tlwu-voice-en.zip` (118 MB): English read-aloud voice (sherpa-onnx vits-ljs, Apache-2.0). Files go to `files/models/tts-en/`. Alternatively the official [`vits-ljs.tar.bz2`](https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/vits-ljs.tar.bz2) unpacked and re-zipped as `vits-ljs.zip`.
   - Russian as the other person's language needs two packs: `tlwu-mobile-lang-ru.zip` (73 MB: Russian speech recognition, Vosk zipformer int8, Apache-2.0, `files/models/asr-ru/`) so the phone can hear them, and `tlwu-voice-ru.zip` (145 MB: Russian read-aloud voice, Supertonic 3 int8, OpenRAIL-M, `files/models/tts-ru/`) to read my translation aloud. The recognizer is loaded only while conversation mode is on with Russian. Then pick **Русский** under 對話模式 -> 對方語言.
   - Building the zips from the upstream archives (maintainers): `python3 -I tool/make_packs.py <WORKDIR> <OUT_DIR>`; upload them to the `mobile-latest` release.
6. You can delete the downloaded zip afterwards. Updating the app: download the new APK from the same page and install over the old one (models stay). If Android says the package conflicts, that preview was signed with a throwaway key (no release key configured yet, see "Signing"): uninstall, install, and import the models again (uninstalling deletes them).

## iOS (same Flutter code as Android)
The Dart app in `lib/` is shared. Only the thin native layer differs: `ios/Runner/TlwuNativeChannel.swift` and
`ios/Runner/ModelImporter.swift` implement the same `tlwu/native` method channel as `MainActivity.kt` /
`ModelImporter.kt` (Files picker + streaming copy + SHA-256; Play asset packs report "unavailable").
Keep the two sides in step: a new channel method needs a Kotlin and a Swift implementation.

- ASR: `sherpa_onnx` and mic: `record` ship their own iOS builds. Translator: `llama_cpp_dart` links its prebuilt
  `llama.xcframework` through the plugin's Swift package (`Translator` uses `LlamaEngine.spawnFromProcess` on iOS), so
  there is no AAR-style build step. Needs Flutter >= 3.44 (CI pins 3.47.6).
- Models: no INTERNET use, same as Android. Download `tlwu-mobile-models.zip` in Safari, then **Import models** and pick it
  in Files. The importer reads the official zip (stored, uncompressed). A zip made by the Files app "Compress" is deflated and is
  rejected: unzip it first and pick the model files.
- 看影片翻譯 (#1076): iOS cannot capture other apps' audio or draw over them, so `ios/Broadcast/` is a ReplayKit
  broadcast upload extension (`com.tinglongwu.mobile.broadcast`) that writes 16 kHz mono PCM16 into a ring file in the
  App Group `group.com.tinglongwu.mobile` (`BroadcastRing.swift`, compiled into both targets). The app
  (`PhoneAudioBridge.swift`) streams it on `tlwu/playback_pcm` like Android, stays alive in the background with silent
  audio (`UIBackgroundModes` audio, mixWithOthers) and shows subtitles in Picture-in-Picture (`SubtitlePiP.swift`).
  No ASR in the extension (50 MB limit). DRM apps (Netflix etc.) deliver silence.
- Build on a Mac: `cd mobile && flutter pub get && flutter build ios --release --no-codesign`
  (CI compile check: `.github/workflows/mobile-ios.yml`). Bundle ID `com.tinglongwu.mobile`, min iOS 15.
- TestFlight (GitHub Actions): job `testflight` in `.github/workflows/mobile-ios.yml` archives, signs (automatic signing
  with an App Store Connect API key) and uploads on every push to `dev` that touches `mobile/**`, and on manual
  *Run workflow*. It skips with a notice while the secrets below are missing; PRs never run it. Owner one-time steps
  (no Mac needed):
  1. developer.apple.com > Identifiers: register the App ID `com.tinglongwu.mobile`.
  2. appstoreconnect.apple.com > Apps: create the app record with that bundle ID.
  3. Users and Access > Integrations > App Store Connect API: create a key with the **App Manager** role; download
     `AuthKey_<KEYID>.p8` (only once) and note the Key ID and Issuer ID. Team ID is under Membership details.
  4. Set the secrets (PowerShell):
     ```
     gh secret set ASC_KEY_ID -R sunososobro-hub/tinglongwu --body "<KEYID>"
     gh secret set ASC_ISSUER_ID -R sunososobro-hub/tinglongwu --body "<ISSUER>"
     gh secret set APPLE_TEAM_ID -R sunososobro-hub/tinglongwu --body "<TEAMID>"
     Get-Content -Raw AuthKey_<KEYID>.p8 | gh secret set ASC_KEY_P8 -R sunososobro-hub/tinglongwu
     ```
  5. 看影片翻譯 (#1076), once: developer.apple.com > Identifiers > App Groups: register `group.com.tinglongwu.mobile`.
     Then open the App IDs `com.tinglongwu.mobile` and `com.tinglongwu.mobile.broadcast` (CI registers the second one),
     tick **App Groups**, Configure, select that group, Save. Until then the `testflight` job warns and uploads
     without the App Group entitlement, so iOS hides 看影片翻譯 (API-key signing cannot create App Groups).
  The build number is the workflow run number. Alternative: `codemagic.yaml` at the repo root (setup steps in its
  header comment) is kept as a fallback.
- Icons: `python3 tool/gen_ios_icons.py` (needs Pillow + `rsvg-convert`).
- Not done yet: Metal for the translator (`gpuLayers: 0`, CPU like Android), and the increased-memory-limit entitlement
  (the 1.1 GB translator plus ASR is tight on 4 GB phones).

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
200 MB base, so they ride in two **fast-follow** Play Asset Delivery packs: `models_asr` (SenseVoice +
Silero VAD, ~240 MB) and `models_llm` (Hy-MT2 GGUF, ~1.13 GB). Play downloads them right after install,
so a Play user never imports anything; the Play Store does the download, so the app still has no
INTERNET permission. CI fills the packs from the same official sources as the models zip
(`tool/fill_asset_packs.py`, size + SHA-256 checked against the manifest; in git the pack folders are
empty) and checks the AAB: both packs fast-follow, each <= 1.5 GB, together <= 4 GB.

The app reads the models in place from the pack folders (`AssetPackManager.getPackLocation`), with no
copy, so they take the space only once. A file already in `files/models/` (imported, adb-pushed, or
copied by the older on-demand flow) always wins and is never replaced or deleted. If the app is
opened before Play has finished, it shows 「正在下載翻譯需要的檔案（第一次使用才需要）」 with a progress
bar and MB done / total, waits for Wi-Fi (or Play's "use mobile data?" dialog), and offers 「重試」
after a failure or with no network; it then goes straight to listening. A side-loaded APK has no packs and keeps **匯入模型 / Import models**.

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
- Optional English voice (`tts-en/`, not in the APK or the models zip): sherpa-onnx `vits-ljs` (VITS trained on LJSpeech), model card Apache-2.0; LJSpeech dataset public domain; lexicon from the CMU Pronouncing Dictionary in IPA (BSD-style).
- `lib/logic/s2tw_data.dart`: generated from the OpenCC dictionaries (Copyright BYVoid and contributors, Apache-2.0, https://github.com/BYVoid/OpenCC), same data as the desktop `opencc-python-reimplemented` package.
- `<MODELS_DIR>` above is wherever you keep the downloaded model files.

## Native AAR (`native/llama-cpp-dart-selfbuilt.aar`)
Built from source (locally by the script, and by CI on every APK build) from upstream llama.cpp commit `afeebe103bd99cda8f5dfaefcabadf890db7fda7` (the commit pinned by `llama_cpp_dart` 23dae0b) with the Android NDK 28.2.13676358 and SDK cmake 3.22.1: arm64-v8a only, Release, `GGML_CPU_ARM_ARCH=armv8.6-a+dotprod+i8mm+fp16`, OpenMP/llamafile/curl off. The script strips `libllama`, `libggml`, `libggml-base`, `libggml-cpu`, `libmtmd` and wraps them in an AAR with an empty `classes.jar`. The binary is committed for convenience and is not reproducible bit for bit (toolchain paths, timestamps). Android users who do not want to trust it can rebuild it themselves and overwrite the file (it prints the new SHA256):

    powershell -File tool\build_llama_android.ps1

The script fetches that exact commit from github.com/ggml-org/llama.cpp, so you can diff the sources you build against upstream.
