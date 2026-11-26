# Listen in English, read Chinese aloud (for people who cannot see): feasibility

Issue #1932. Owner (2026-11-25, phone): the app should listen to English and read the Chinese translation aloud. This is a written report only; no app code or model is changed, and no child issue is opened (the product window opens them).

**Status of this report.** Section 2 is measured from the repo (the command below prints the counts). Section 3 is a design plus a proposed budget, nothing measured on a phone. Section 1 is **partial**: the cloud run that wrote it could not open the matcha / kokoro pages or the model cards, so the licence column is marked unverified and the child issue C1 closes that gap before any pick is final.

## 1. Chinese voice for the phone

What exists: the phone reads aloud through sherpa-onnx `OfflineTts` (`mobile/lib/services/tts_worker.dart`, sherpa_onnx 2.0.1 in `mobile/pubspec.yaml`) and plays through `TtsPlayer` (Android AudioTrack). Voice packs today: `tlwu-voice-en` (vits-ljs, 118 MB) and `tlwu-voice-ru` (Supertonic, 145 MB) (`mobile/README.md`). The worker already has a VITS path, so a VITS-format Chinese model needs a new pack layout and no new engine.

Candidates (size, rate and speakers are the "Model filesize (MB)", sample-rate and speaker columns of the [sherpa-onnx VITS model list](https://k2-fsa.github.io/sherpa/onnx/tts/pretrained_models/vits.html); the list gives no licence):

| model | download | size MB | rate Hz | speakers | licence (must allow redistribution in a GitHub release pack) |
|---|---|---|---|---|---|
| vits-melo-tts-zh_en (zh + en) | [vits-melo-tts-zh_en.tar.bz2](https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/vits-melo-tts-zh_en.tar.bz2) | 163 | 44100 | 1 | converted from [myshell-ai/MeloTTS-Chinese](https://huggingface.co/myshell-ai/MeloTTS-Chinese); upstream licence believed MIT, **not verified in this run** |
| sherpa-onnx-vits-zh-ll (zh) | [sherpa-onnx-vits-zh-ll.tar.bz2](https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/sherpa-onnx-vits-zh-ll.tar.bz2) | 115 | 16000 | 5 | not stated; **unverified** |
| kokoro multi-lang (zh + en), see [sherpa Kokoro zh-en page](https://k2-fsa.github.io/sherpa/onnx/tts/all/Chinese-English/kokoro-multi-lang-v1_0.html) and [Kokoro-82M-v1.1-zh](https://huggingface.co/hexgrad/Kokoro-82M-v1.1-zh) | see page | not read | not read | many | upstream believed Apache-2.0, **not verified**; needs a Kokoro config path in the worker |

Recommended pick (provisional): **vits-melo-tts-zh_en**. Reasons: one model reads Chinese and the English words that appear inside Chinese sentences; 44.1 kHz is the clearest of the list; the VITS config path already exists in the worker. Costs: 163 MB is larger than the existing packs, and the model card says English words only work if they are in its lexicon. The runner-up is sherpa-onnx-vits-zh-ll (smaller, 16 kHz, lower quality). Not picked or checked: matcha-icefall-zh-baker (the Baker dataset licence has to be read before any redistribution).

How to finish: C1 below.

## 2. Screen reader (TalkBack)

`grep` counts per screen (run from the repo root; "interactive" is a rough count of tappable widget constructors, "semantics" counts `Semantics(` / `semanticLabel`, "tooltip" counts `tooltip:`; Flutter's `Tooltip` / `tooltip:` gives TalkBack a spoken label, but a bare `GestureDetector` has no button role):

    for f in mobile/lib/ui/*.dart; do printf '%-45s interactive=%s semantics=%s tooltip=%s\n' "$f" "$(grep -cE 'IconButton\(|ElevatedButton|FilledButton|OutlinedButton|TextButton|InkWell\(|GestureDetector\(|SwitchListTile|Switch\(|SegmentedButton|PopupMenuButton|ListTile\(' "$f")" "$(grep -cE 'Semantics\(|semanticsLabel|semanticLabel' "$f")" "$(grep -c 'tooltip:' "$f")"; done

Output at the time of writing:

    mobile/lib/ui/advanced_screen.dart            interactive=3 semantics=0 tooltip=0
    mobile/lib/ui/conversation_sheet.dart         interactive=2 semantics=0 tooltip=0
    mobile/lib/ui/download_screen.dart            interactive=3 semantics=0 tooltip=0
    mobile/lib/ui/history_screen.dart             interactive=5 semantics=0 tooltip=1
    mobile/lib/ui/import_screen.dart              interactive=5 semantics=0 tooltip=0
    mobile/lib/ui/licences_screen.dart            interactive=1 semantics=0 tooltip=0
    mobile/lib/ui/message_screen.dart             interactive=4 semantics=0 tooltip=0
    mobile/lib/ui/pack_install_screen.dart        interactive=5 semantics=0 tooltip=0
    mobile/lib/ui/subtitle_bubble.dart            interactive=0 semantics=0 tooltip=0
    mobile/lib/ui/subtitle_screen.dart            interactive=20 semantics=0 tooltip=5
    mobile/lib/ui/version_label.dart              interactive=0 semantics=0 tooltip=0

No file in `mobile/lib` has a `Semantics` widget or `semanticLabel`. The 5 most important to fix for the listen -> speak path:

1. Start / stop button: `subtitle_screen.dart` `GestureDetector key micButton` (round mic, in a `Tooltip`). Needs `Semantics(button: true, label, onTap)` and a spoken state change ("listening" / "stopped").
2. Mode choice: `SegmentedButton<SubtitleMode>` and the 切換翻譯方式 `PopupMenuButton` in `subtitle_screen.dart`. Selected state must be announced.
3. First-use download: `download_screen.dart` (status text, 重試, 改用行動數據下載) and `message_screen.dart`. Progress and errors are plain `Text`; needs a live region so progress is spoken.
4. Permission line: the pre-dialog line proposed in #1928 (not built yet); it must be a focusable, spoken message with the retry / open-settings buttons labelled.
5. Settings: the 字幕顯示設定 `IconButton` and its `SwitchListTile`s (`subtitle_screen.dart`, `advanced_screen.dart`), plus the live subtitle text itself (new subtitle lines are not announced).

## 3. Overlap and delay

Problem: the English audio keeps playing while the Chinese voice speaks. Options:

- **Earphone-only**: read the translation only when a headset is connected; the original plays on the speaker or in the video. Simplest, hard to confuse, but not for phone-speaker use.
- **Duck the video**: request transient audio focus with "may duck" so the other app lowers its volume while the voice speaks (Android `AudioManager.requestAudioFocus`; the app does not request audio focus today, `TtsPlayer.kt`/AudioTrack only). Works for most players; some apps ignore ducking.
- **Between sentences**: queue a sentence and speak it in the next VAD silence, never over speech; delay grows with long sentences.

Proposal: earphone-only by default, duck when no headset, and queue so one sentence never overlaps another (`TtsPlayer.play` today replaces what is still playing, so a long Chinese sentence is cut by the next one; a queue is needed).

Existing latency, `docs/latency.md` (desktop PC with RTX 3050, 2026-11-12, smoke runs n=3 to 6, indicative only): end of speech -> subtitle shown 1.69 to 2.90 s for English listen; speak path 2.34 s with first TTS audio 0.41 s. These are not phone numbers.

Proposed budget (a proposal, not a measurement): end of speech -> start of Chinese speech at most **3.0 s at p50 and 4.5 s at p90** on a mid-range phone. How to measure on a phone: play a clip with known end-of-speech times (a WAV with a click marker) into the mic, log timestamps in the app at VAD end, translation done and `TtsPlayer.play` started, run at least 30 utterances (rules section 20: at least 10 baseline runs before it becomes acceptance), report p50 / p90.

## Child issues (not opened)

- C1 Why: licence and size of the zh voice are unverified. Goal: pick the zh TTS model. Acceptance: licence text and archive size of the 3 candidates quoted with links, one pick, redistribution OK. Verify: links open.
- C2 Why: no zh voice on the phone. Goal: `tlwu-voice-zh` pack (manifest, layout, download) using the VITS path. Acceptance: pack imports, a zh sentence plays on a phone. Verify: `cd mobile && flutter test` plus a device check.
- C3 Why: TalkBack cannot operate the start button. Goal: Semantics on the 5 controls in section 2. Acceptance: the grep command shows semantics > 0 on those screens and a widget test finds the labels. Verify: `flutter test`, TalkBack on a phone.
- C4 Why: translations overlap the original audio. Goal: headset detection, audio-focus ducking and a speech queue in `TtsPlayer`. Acceptance: two sentences never play at once (unit test); the video volume drops while speaking. Verify: device check.
- C5 Why: the new listen -> speak mode needs a latency budget. Goal: add the phone latency log and measure. Acceptance: p50 / p90 over at least 30 utterances against the 3.0 / 4.5 s proposal. Verify: the log in the PR.
