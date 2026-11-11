# Install (from source), releases and GPU

Moved from the README (#166). Quick one-line installs stay in the [README](../README.md#one-command-install-windows--mac--linux).

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
- **macOS**: install [BlackHole 2ch](https://github.com/ExistentialAudio/BlackHole), create a Multi-Output Device (speakers + BlackHole) in Audio MIDI Setup and make it the output. tlwu captures the device whose name contains `BlackHole`. ScreenCaptureKit audio capture is a possible future option.

### GPU (optional, NVIDIA)
`--device auto|cpu|cuda` (default `auto`: CUDA if CTranslate2 sees an NVIDIA GPU and the libraries load, else CPU). GPU runs Whisper in float16 and OPUS/M2M in int8_float16; CPU uses int8. What you need, once (with network):
```
tlwu-setup --gpu     # = pip install nvidia-cublas-cu12 nvidia-cudnn-cu12 (CUDA 12 cuBLAS + cuDNN 9; no CUDA toolkit needed)
```
plus an NVIDIA driver that supports CUDA 12 or newer. If the GPU or these libraries are missing, tlwu prints a clear message and falls back to CPU (with `--device cuda` too). Models never leave your machine; the libraries are only loaded locally. `large-v3` in float16 needs about 4 GB of VRAM, `medium` about 2.5 GB.
