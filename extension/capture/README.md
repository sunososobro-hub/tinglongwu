# tlwu Chrome extension: tab audio capture (#1159, parent #711)

Part 2 of the Chrome extension (#711): click the toolbar button on a meeting tab and the tab's audio becomes 16 kHz
mono frames for on-device speech recognition. Nothing leaves the PC: the manifest asks only for `tabCapture` and
`offscreen`, no host permissions, and every script is packaged (MV3 forbids remote code).

| file | role |
| --- | --- |
| `manifest.json` | MV3, permissions `tabCapture` + `offscreen` only |
| `sw.js` | service worker: action click -> `chrome.tabCapture.getMediaStreamId` -> offscreen document; click again stops |
| `offscreen.html` / `offscreen.js` | opens the tab stream, plays it back (Chrome mutes a captured tab), feeds the frames |
| `pcm-worklet.js` | AudioWorklet: mono mix, downsample to 16 kHz, 100 ms frames (1600 samples); speaker-path meter |
| `resample.js` | streaming downsampler (any rate >= 16 kHz), no drift across blocks |
| `recognizer.js` | the recognizer hook: `setRecognizer({onFrame(frame)})`; the default only counts frames |

Plugging in a recognizer (part 1 #1158 chose whisper-small + opus-mt): in the offscreen document,
`import { setRecognizer } from "./recognizer.js"` and pass an object whose `onFrame(Float32Array)` queues the frame
(it runs on the main thread of the offscreen document; inference belongs in a worker).

Known limit: `cross_origin_embedder_policy` (needed for multi-threaded WASM, #1158 RESULTS.md) makes the offscreen
document fail to open the tab stream (`getUserMedia` -> "Error starting tab capture", STREAM_NOT_FOUND_IN_REGISTRY in
Chromium 153), so this manifest does not set it. Part 3 must run threaded inference somewhere else (e.g. a separate
cross-origin-isolated extension page fed through a MessagePort) or use single-threaded WASM / WebGPU.

## Subtitle overlay (#1160)
`overlay.js` is a content script (https Meet / Teams / Zoom pages only) that stays inert until it gets a line. A
recognizer calls `emitLine({src, text})` (exported by `offscreen.js`); the line goes offscreen -> service worker ->
the captured tab, where a movable overlay (drag the top grip) shows the last 3 lines: source small, translation
large. It is removed when capture stops. Test: `tests/test_extension_overlay_1160.py`.

## Try it
1. `chrome://extensions` -> Developer mode -> Load unpacked -> this folder.
2. Open a tab that plays audio, click the toolbar button: the badge shows `ON`, the tab stays audible. Click again to
   stop.

## Test
`python -m pytest -q tests/test_extension_capture_1159.py` (needs `pip install playwright==1.63.0`,
`python -m playwright install chromium` and node). CI runs it in the `ci` `test` job with `TLWU_REQUIRE_EXTENSION=1`.
The test loads the extension in headless Chromium with `--allowlisted-extension-id` in place of the toolbar click
(automation cannot click the toolbar) and plays a 3 s 440 Hz WAV.
