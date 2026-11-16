# Chrome Web Store listing draft (#1161, parent #711)

Build the package: `python scripts/build_extension_zip.py` -> `dist/tlwu-extension-<version>.zip`.
Submission and the developer account fee are an owner step.

## Status: do not submit yet
The listing text below describes the finished product. The packaged extension currently captures tab audio and has a
subtitle overlay, but the default recognizer (`extension/capture/recognizer.js`) only counts frames and produces no
subtitle lines or translation. Submit only after the recognizer is integrated (#711); until then the package is a
capture prototype.

## Name
tlwu live subtitles

## Short description (<= 132 chars)
Offline live subtitles for meeting tabs (Meet, Teams, Zoom). Tab audio never leaves your PC.

## Detailed description
tlwu live subtitles turns the audio of a meeting tab into on-screen subtitles. Click the toolbar button on a Google
Meet, Microsoft Teams or Zoom tab: the tab's audio is captured, recognized and translated on your own computer, and a
movable overlay shows the last lines. Click again to stop. Works on locked-down PCs where only Web Store extensions
can be installed.

## Privacy statement
- No audio, text or usage data is sent to any server. The extension makes no network requests and has no host
  permissions; all scripts are packaged (no remote code).
- Tab audio is processed in memory on this PC and is not recorded or saved.
- No analytics, no accounts, no ads. Nothing is sold or shared with third parties.
- The only stored value is the id of the currently captured tab (`chrome.storage.session`, cleared when the browser
  closes).

## Permission justifications
- `tabCapture`: capture the audio of the tab the user clicked the button on; this is the extension's single purpose.
- `offscreen`: an MV3 service worker cannot play or process audio, so a hidden offscreen document opens the captured
  stream, keeps the tab audible and downsamples it to 16 kHz for recognition.
- `storage`: remember which tab is being captured when Chrome restarts the idle service worker.
- Content script on `meet.google.com`, `teams.microsoft.com`, `teams.live.com`, `*.zoom.us`: draw the subtitle
  overlay on the captured meeting tab; it reads nothing from the page and makes no network request.

## Single purpose
Show live, on-device subtitles for the audio of a meeting tab.
