# Install (from source), releases and GPU

Moved from the README (#166). Quick one-line installs stay in the [README](../README.md).

## What you might see during install

tinglongWu is a small open-source project and does not pay for a Windows code-signing certificate or an Apple Developer account, so a few warnings are normal. Every release lists SHA256 checksums of all its files in `SHA256SUMS.txt` if you want to verify a download.

- **Windows:** `tinglongwu-setup.exe` is not code-signed (see [Releases](#releases-read-before-trusting-a-binary)). Microsoft SmartScreen may show "Windows protected your PC": click **More info**, then **Run anyway**. Some antivirus tools may also flag unsigned builds. It installs for your user only (no admin prompt) into `%LOCALAPPDATA%\Programs\tinglongWu`. The download is small because the wizard fetches the rest while it runs, so stay online: it installs Python 3.12 if it is missing, then about 3 GB of Python packages plus the speech/translation models (about 2 GB, about 3 GB with an NVIDIA GPU of 4 GB or more), with a progress bar. Expect 10-40 minutes depending on your connection. Accept the license on the first page; after that, Next is enough. It detects an NVIDIA GPU by itself; the wizard's CPU option forces the CPU path. It adds Start-menu and desktop shortcuts; uninstall it from Settings > Apps. Prefer a portable build? [tinglongwu-win64.zip](https://github.com/sunososobro-hub/tinglongwu/releases/latest/download/tinglongwu-win64.zip): extract it and double-click `tlwu.exe` (if it does not start, install the Microsoft Visual C++ Redistributable 2015-2022 x64; it asks before downloading models on first run).
- **macOS:** there is no app to double-click, so there is no "unidentified developer" prompt for tinglongWu itself. The installer's messages are in Traditional Chinese. It needs Homebrew first: if it is missing, the script stops and prints the official install command (https://brew.sh); follow its "Next steps", open a new Terminal window, and run the command again. Homebrew then installs Python 3.12, git and the BlackHole 2ch/16ch audio drivers; the driver install asks for your Mac password (typing shows nothing). If a BlackHole install fails (wrong password or blocked by security settings), just run the command again. A restart may be needed before BlackHole shows up. One manual step remains, printed at the end: in Audio MIDI Setup create a Multi-Output Device (your speakers/headphones + BlackHole 2ch) and use it as the sound output; in Meet/Zoom pick **BlackHole 16ch** as the microphone. On first run macOS asks to let Terminal use the microphone: click OK (or enable it later in System Settings > Privacy & Security > Microphone).
- **Linux:** run it as your normal user, not root. It needs Python 3.12 (Ubuntu 24.04's default). Before any `apt` step it lists the missing packages and asks y/N; yes asks for your sudo password. It downloads the Kokoro English voice (about 120 MB); speech/translation models are only downloaded later, after the menu asks.

## Releases (read before trusting a binary)

The Windows release zip is **unsigned** and built locally by the maintainer with Nuitka; the build is **not reproducible**. Its SHA256 is published in the release notes: compare it with `Get-FileHash tinglongwu-win64.zip` before running. The hash only proves you got the file the maintainer published. If you want real verification, install from source (`install.bat` or the steps below): the source, pinned dependencies and model hashes are all auditable.

## Install

Python 3.12 (the pinned numpy/scipy need >= 3.12; tested 3.12).
```
tlwu-setup --deps  # or: pip install -r requirements.txt && pip install -e . --no-deps    # exact pins
```

Hash-verified install (recommended): `requirements.lock.txt` lists every dependency (including transitive ones) with all PyPI SHA256 hashes, so pip refuses any tampered or substituted file:
```
pip install --require-hashes -r requirements.lock.txt && pip install -e . --no-deps
```
`requirements.txt` (plain `==` pins, no hashes) keeps working. Regenerate the lock with `python scripts/make_lock.py` (`httpx2`/`httpcore2` are not typosquats: they are the Pydantic-maintained httpx fork that `huggingface_hub` 2.x depends on).

Updating a Windows dev checkout (git clone on the `dev` branch): double-click **`update.bat`**: it fast-forwards `dev`, reinstalls the pinned packages and starts the GUI (stops with a message on local changes or another branch).

- **Windows**: nothing extra (WASAPI loopback of the default speakers).
- **Linux**: PipeWire (with pipewire-pulse) or PulseAudio. Default captures the monitor of the default sink; list with `pactl list sources short` and choose with `--capture-device <substring>`.
- **Linux GUI** (`tlwu-gui`): tkinter, i.e. `python3-tk` on Debian/Ubuntu (`install.sh` checks for it and installs it with the other apt packages).
- **macOS**: install [BlackHole 2ch](https://github.com/ExistentialAudio/BlackHole), create a Multi-Output Device (speakers + BlackHole) in Audio MIDI Setup and make it the output. tlwu captures the device whose name contains `BlackHole`. ScreenCaptureKit audio capture is a possible future option.

### GPU (optional, NVIDIA)
`--device auto|cpu|cuda` (default `auto`: CUDA if CTranslate2 sees an NVIDIA GPU and the libraries load, else CPU). GPU runs Whisper in float16 and OPUS/M2M in int8_float16; CPU uses int8. What you need, once (with network):
```
tlwu-setup --gpu     # = pip install nvidia-cublas-cu12 nvidia-cudnn-cu12 (CUDA 12 cuBLAS + cuDNN 9; no CUDA toolkit needed)
```
plus an NVIDIA driver that supports CUDA 12 or newer. If the GPU or these libraries are missing, tlwu prints a clear message and falls back to CPU (with `--device cuda` too). Models never leave your machine; the libraries are only loaded locally. `large-v3` in float16 needs about 4 GB of VRAM, `medium` about 2.5 GB.

## Quick install (Windows)

For Windows 10/11 with an NVIDIA GPU (works without one, just slower). If Python 3.12 is missing, the installer installs Python 3.12 for your user account itself (no admin; SHA256-checked download from python.org, winget as fallback).

1. On GitHub click **Code > Download ZIP**, then right-click the ZIP > **Extract All**.
2. Open the folder and double-click **`install.bat`**. Wait (10-30 min, up to ~3 GB of models on an NVIDIA GPU with 4 GB+ VRAM, ~1 GB otherwise; safe to re-run if interrupted).
3. Double-click the new desktop shortcut **聽攏有 tinglongWu**.

**Using the menu.** The shortcut opens a menu: `1` Meeting (listen + speak into Meet/Zoom, wear a headset; set the meeting microphone to *CABLE Output*), `2` Speak only (Chinese -> English/Russian/Japanese voice), `3` Listen only (foreign speech -> Chinese subtitles), `L` change the other side's language, `G` graphical interface, `U` update now, `A` toggle auto-update at start, `I` menu language. Press the key and Enter; every model download asks y/N first.

To remove: delete the folder and the shortcut. `install.ps1 -DryRun` only shows what would be done. Hy-MT2 on the GPU uses `requirements-llama-cuda.txt` (CUDA build of llama-cpp-python); if no matching wheel exists the installer falls back to the OPUS engine automatically.

## Mac one-line install

Apple Silicon and Intel, macOS 12+. In Terminal:

```
curl -fsSL https://github.com/sunososobro-hub/tinglongwu/releases/latest/download/mac_install.sh | bash
```

It checks Homebrew (never installs it silently; prints the official command if missing), installs Python 3.12, git, BlackHole 2ch and 16ch, clones to `~/tinglongwu`, creates the venv, sets up the English voice (Kokoro, own venv in `~/tts-kokoro`, model in `~/models/kokoro`), downloads the models, and installs `tlwu-menu`. Re-running is safe (repair/update). The one manual step it prints at the end: in Audio MIDI Setup create a Multi-Output Device (speakers/headphones + BlackHole 2ch) and set it as the system output; set the Meet/Zoom microphone to **BlackHole 16ch**. Audio design: BlackHole 2ch = capture of system audio (listening), BlackHole 16ch = virtual microphone for the synthesized speech, so your own TTS never loops into listening. Run `tlwu-menu` afterwards; on the first menu run it asks once whether to check GitHub for updates at every menu start (`git pull --ff-only`, about 6 s cap, offline-safe); if you say no, nothing touches the network and `U` in the menu updates on demand (`TLWU_NO_UPDATE=1` forces it off). Japanese/Russian speech are not part of the Mac install. Untested on real Macs so far.

## One-command install details (moved from the README, #342)
Each command installs the exact version of the latest GitHub Release (`main` is stable; `dev` is for development). Re-running is safe.

Each release also lists `SHA256SUMS.txt`; to check a script before running it, download it and compare. The Linux installer asks y/N before any `apt` step and downloads no ASR/MT models (the menu asks before every download). Unattended: `curl -fsSL https://github.com/sunososobro-hub/tinglongwu/releases/latest/download/install.sh | TLWU_YES=1 bash` (or `bash install.sh --yes`) answers yes to apt, asks nothing else, and pre-downloads the default first-run ASR/MT models (SHA256-checked, same tier as the first-run setup); the Russian voice is never fetched this way (no upstream license). The PowerShell source installer `scripts/bootstrap_windows.ps1` downloads the source to `%USERPROFILE%\tinglongwu` and runs the same `install.ps1` as below (the execution-policy bypass is for that one child process only).

Windows ZIP install (`install.bat`) and the menu keys, Mac details, source install, GPU are described above. The Windows release zip is unsigned and not reproducible: [read this first](#releases-read-before-trusting-a-binary).

**Linux/macOS menu, speak mode:** each translated sentence is sent directly (no confirm question). To confirm each sentence first, run `bash scripts/linux_menu.sh --confirm` (macOS: `tlwu-menu --confirm`), or set `TLWU_CONFIRM=1`. The menu asks only what it must: output choice (and device), plus the optional transcript-save question in meeting mode.
