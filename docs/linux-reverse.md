# Linux reverse mode (experimental, untested)

[Back to README](../README.md)


> UNTESTED: written on Windows without a Linux machine; only the command construction is unit-tested. Expect rough edges.

Reverse mode (you speak Chinese, Russian speech goes into Meet/Zoom) on a CPU-only Linux laptop, e.g. AMD Ryzen 7040:

```
sudo apt install pulseaudio-utils            # pactl (PipeWire: pipewire-pulse already provides it)
tlwu-setup --whisper small --pair zh-en --pair en-ru --pair ru-en --pair en-zh   # one-time, needs network
python -m tinglongwu.reverse.proto --list-mics
python -m tinglongwu.reverse.proto --mic "<your mic substring>" --compute cpu --mt opus     --tts-engine vosk --tts-python /path/to/tts-vosk/bin/python --tts-model /path/to/vosk-model-tts-ru-0.7-multi
```

- On Linux the program creates a virtual microphone automatically (`pactl load-module module-null-sink sink_name=tlwu_sink` + `module-remap-source ... source_name=tlwu_mic`), plays the Russian audio to `tlwu_sink`, and unloads both modules on exit. In Meet/Zoom choose the microphone `tlwu_mic`. `--no-vmic` disables this (then pass `--device <sink substring>`); `--list-speakers` lists output devices. Windows keeps VB-CABLE.
- Without CUDA, translation uses the OPUS/Argos pivot zh -> en -> ru (needs the four `--pair` models above); Whisper defaults to `small`. `--mt qwen` forces Qwen3 (GPU).
- The Russian voice model is **optional and opt-in** (no upstream license): no installer or default setup fetches it. Get it with `tlwu-setup --vosk-tts-ru` (or the menu prompt, y/N, default N, which first prints "Russian voice model license is not stated upstream; download at your own responsibility"). Without it, speaking Russian shows subtitles only and prints how to enable it.
- Speech uses fixed built-in voices on CPU: Vosk-TTS for Russian (`--tts-engine vosk`, voice ids 0-4 via `--speaker`) and Kokoro for English/Japanese (`--tts-engine kokoro`), each in its own venv. There is no reference-audio voice cloning.
