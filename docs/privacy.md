# Privacy model and install / use split

Moved from the README (#166). See also [PRIVACY.md](../PRIVACY.md) and [SECURITY.md](../SECURITY.md).

## Install / use split (read this first)

One repo, two programs with a hard boundary:

| | `tlwu-setup` (package `tinglongwu_setup`) | `tlwu` (package `tinglongwu`) |
|---|---|---|
| Network | **Yes** - the only networked code | **Never** - zero download/network code |
| Does | installs pinned deps, downloads + SHA256-pins models, builds/installs offline bundles | captures audio, transcribes, translates, prints |
| When | once, while online | every run, can be fully offline |

`tests/test_no_network_code.py` statically scans `tinglongwu/` and fails if it imports requests, urllib.request, http.client, huggingface_hub, argostranslate, socket (except the guard module `tinglongwu/offline.py`) or `tinglongwu_setup`. The one exception is `http.client` in `tinglongwu/ollama.py`: the opt-in `--engine ollama` (#356) talks to your own Ollama server and accepts loopback addresses only (the runtime network guard blocks anything else too).

```
tlwu-setup --deps                                  # pip install the pinned requirements
tlwu-setup --whisper small --langs ru --to zh      # download + pin models
tlwu-setup --bundle tlwu-bundle.zip                 # pack wheels + models for an air-gapped machine
tlwu-setup --from-bundle tlwu-bundle.zip            # on the offline machine: install + verify, no network
```
Bundles are platform-specific: build on the same OS, CPU architecture and Python minor version as the target.

## Privacy model

- Audio lives in memory only by default; the opt-in `--record` saves it on this computer (one WAV per line + `segments.jsonl`, never uploaded). Transcripts go to stdout only; nothing is logged. Only the explicit opt-ins `--save` and `--save-result PATH` (speak mode) write text to disk.
- On `import tinglongwu` (before any HF/ctranslate2 import) the env is forced offline: `HF_HUB_OFFLINE`, `TRANSFORMERS_OFFLINE`, `HF_HUB_DISABLE_TELEMETRY`, `DO_NOT_TRACK`, `ARGOS_*`.
- A runtime **network guard** patches `socket.connect/connect_ex/create_connection/getaddrinfo/gethostbyname` and raises on any non-loopback target (loopback and AF_UNIX pass). It is on in `tlwu`, `tlwu --gui`, `python -m tinglongwu.reverse.proto` and the TTS worker subprocesses (Kokoro/Vosk). Only our own processes are affected; no OS firewall is touched. `tlwu-setup` and the update step (`git`) do not use the guard; `TLWU_ALLOW_NET=1` disables it.
- Network is for **install/setup** (tlwu-setup, pip) and **update** (git) only. Updating is opt-in: the first menu run asks once whether to check GitHub on every menu start (saved in the user config: `~/.config/tinglongwu/config`, `~/Library/Application Support/tinglongwu/config`, `%APPDATA%\tinglongwu\config`); if off, the menu never touches the network and `U` updates on demand. `TLWU_NO_UPDATE=1` forces it off.
- Update-check counter (#806, Windows wizard installs): only the automatic once-a-day check (`auto_update=1`) goes through our Cloudflare Worker (`cloudflare/update-counter`), which returns GitHub's latest-release answer unchanged and adds 1 to a count keyed only by (date, app version, OS). It stores no IP, no user agent, no ID. A manual "Check for updates" click and the download itself go straight to GitHub; with auto-update off nothing is sent. Worker down -> the app asks GitHub as before.
- Every blocked attempt prints one line and appends a JSON line (time, host, port, mode, package guessed from the stack, trimmed stack without any content, tlwu version, OS) to `netguard.jsonl` in the user log dir (`~/.local/state/tinglongwu`, `~/Library/Logs/tinglongwu`, `%LOCALAPPDATA%\tinglongwu`). Nothing is ever sent automatically: the next menu start shows what was blocked and asks "report to the developer? [y/N]"; `y` shows the exact text and opens a pre-filled GitHub issue in your browser that you submit yourself.
- Menu language follows the system (English by default, Russian, Traditional Chinese); override with `TLWU_UI_LANG=en|ru|zh-TW` or `I` in the menu. Texts live in `scripts/i18n/` and `tinglongwu/i18n.py`; adding a language = adding a file.
- Models load only from a local `models/` dir (`--models-dir` or `TLWU_MODELS`); missing models give an error pointing to the download script. Nothing is auto-downloaded.
- `tlwu-setup` (`tinglongwu_setup`) is the **only** program that uses the network. It pins on first download (HF commit sha / package URL + SHA256 of every file; committed pins in `tinglongwu_setup/manifest.json`, local record in `models/manifest.json`) and verifies on every later run (`--verify` works offline).
- `scripts/verify_offline.py` runs tinglongWu and polls the whole process tree with psutil; fails on any non-loopback connection.
- The guard is in-process defence in depth, **not proof**. It only blocks Python-level sockets (`connect`, `connect_ex`, `sendto`, name resolution; our TTS worker subprocesses load the same guard). Native libraries that open their own sockets (ctranslate2, onnxruntime, llama.cpp, other C extensions) and other subprocesses are **not** covered. CI runs the tests inside an empty network namespace (`scripts/ci_offline.sh`) as an extra check. The firewall test below is the real, independent proof.

### Firewall block test (per platform)

One command runs listen, speak and meeting with the network cut and checks each still translates (Windows: admin terminal, adds and always removes an outbound block rule for the Python it uses; Linux: empty network namespace; macOS: block first, then add `--already-blocked`):
```
python scripts/firewall_test.py --listen-wav talk.wav --speak-wav zh.wav   # exit 0 = PASS
```
Add `--tts-python <kokoro venv python> --tts-model <kokoro dir>` to include speech synthesis. Manual steps:

Windows (admin PowerShell), block outbound for the Python you use:
```powershell
New-NetFirewallRule -DisplayName "tlwu-block" -Direction Outbound -Action Block -Program (Get-Command python).Source
python -m tinglongwu --file talk.wav      # must still work
Remove-NetFirewallRule -DisplayName "tlwu-block"
```
Linux: `scripts/linux_offline_run.sh --file talk.wav` (Ubuntu 23.10+ AppArmor breaks `unshare -rn`; the script uses sudo unshare + setpriv, firejail fallback), or `unshare -n -r python -m tinglongwu --file talk.wav` (no network namespace; live capture via a unix socket still works only if PulseAudio is reachable - use `--file` for the test), or an nftables rule: `nft add rule inet filter output meta skuid $UID drop`.
macOS: `pf` rule (`block drop out quick all user $USER` loaded with `pfctl -f`), or Little Snitch / LuLu set to deny the Python binary.

Then `python scripts/verify_offline.py --file talk.wav`.
