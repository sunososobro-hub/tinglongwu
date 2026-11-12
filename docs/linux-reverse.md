# Linux reverse mode (experimental, untested)

> UNTESTED: written on Windows without a Linux machine; only the command construction is unit-tested. Expect rough edges.

Reverse mode (you speak Chinese, Russian speech goes into Meet/Zoom) on a CPU-only Linux laptop, e.g. AMD Ryzen 7040:

```
sudo apt install pulseaudio-utils            # pactl (PipeWire: pipewire-pulse already provides it)
pip install llama-cpp-python --only-binary=:all: --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu
tlwu-setup --whisper small --hymt                 # one-time, needs network (Hy-MT2 GGUF, about 1.1 GB)
python -m tinglongwu.reverse.proto --list-mics
python -m tinglongwu.reverse.proto --mic "<your mic substring>" --compute cpu --tts-engine vosk --tts-python /path/to/tts-vosk/bin/python --tts-model /path/to/vosk-model-tts-ru-0.7-multi
```

- On Linux the program creates a virtual microphone automatically (`pactl load-module module-null-sink sink_name=tlwu_sink` + `module-remap-source ... source_name=tlwu_mic`), plays the Russian audio to `tlwu_sink`, and unloads both modules on exit. In Meet/Zoom choose the microphone `tlwu_mic`. `--no-vmic` disables this (then pass `--device <sink substring>`); `--list-speakers` lists output devices. Windows keeps VB-CABLE.
- Translation is always Hy-MT2 direct (zh -> ru / en / ja and back, no pivot through English); it is the only engine. It needs `llama-cpp-python` in the main venv and `tlwu-setup --hymt`; if either is missing the program stops with the install command instead of switching engines. Hy-MT2 runs on CUDA when the GPU has 2 GB free, else on the CPU.
- CPU expectations: Hy-MT2 takes about 1-1.5 s per sentence on a laptop CPU (0.93 s on an i5-13500, #66), so speech comes out about 4 s after you stop talking (#186); the program prints this at startup. Whisper defaults to `small` without CUDA. `--pause` (= `--end-silence`, 0.3-2.0 s) sets the silence that ends a sentence (lower = audio starts sooner).
- The Russian voice model is **optional and opt-in** (no upstream license): no installer or default setup fetches it. Get it with `tlwu-setup --vosk-tts-ru` (or the menu prompt, y/N, default N, which first prints "Russian voice model license is not stated upstream; download at your own responsibility"). Without it, speaking Russian shows subtitles only and prints how to enable it.
- Speech uses fixed built-in voices on CPU: Vosk-TTS for Russian (`--tts-engine vosk`, voice ids 0-4 via `--speaker`) and Kokoro for English/Japanese (`--tts-engine kokoro`), each in its own venv. There is no reference-audio voice cloning.
