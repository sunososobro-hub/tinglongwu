"""Speak-mode audio output (split from proto.py, #663): the Player thread and the VRAM sampler."""
from __future__ import annotations

import queue
import subprocess
import sys
import threading

import numpy as np


def vram_used_mib() -> int | None:
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10).stdout
        return int(out.strip().splitlines()[0])
    except Exception:  # noqa: BLE001
        return None


class VramSampler:
    def __init__(self, period: float = 0.25):
        self.period, self.peak, self._stop = period, 0, threading.Event()
        self.t = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self._stop.is_set():
            v = vram_used_mib()
            if v:
                self.peak = max(self.peak, v)
            self._stop.wait(self.period)

    def __enter__(self):
        self.t.start()
        return self

    def __exit__(self, *a):
        self._stop.set()
        self.t.join(2)


PLAY_BLOCK_S = 0.1  # #427: a piece is written in blocks this long, so abort() is noticed within one block
ABORT_JOIN_S = 2.0  # after abort(): wait for the playback thread (one PLAY_BLOCK_S block + closing the stream)


class Player:
    """Plays 24 kHz mono float chunks on the chosen output device (resampled to `out_sr`) from a worker thread."""

    def __init__(self, device: str | None, out_sr: int = 48000, enabled: bool = True):
        self.out_sr, self.enabled, self.q = out_sr, enabled, queue.Queue()
        # macOS: BlackHole 16ch has 16 channels; a mono stream may reach only channel 1, so send identical L/R
        self.ch = 2 if sys.platform == "darwin" else 1
        self.speaker_name = None
        from .live import PlaybackGate

        self.gate = PlaybackGate()
        self._abort = threading.Event()
        if enabled:
            import soundcard as sc

            from ..capture._soundcard import patch_mixformat
            from .logic import match_device
            from .vmic import loop_prone_output

            patch_mixformat()  # #130: players hit the same soundcard mix-format assert as recorders
            speakers = sc.all_speakers()
            idx = match_device([s.name for s in speakers], device)
            spk = speakers[idx] if idx is not None else sc.default_speaker()
            self.speaker_name = spk.name
            if sys.platform == "darwin" and loop_prone_output(spk.name):
                raise RuntimeError(
                    f"output device {spk.name!r} is the system-audio capture path (it would be heard by the "
                    f"listen side and loop back). Pass --device with your headphones / 'BlackHole 16ch' "
                    f"(see --list-speakers).")
            self.t = threading.Thread(target=self._run, args=(spk,), daemon=True)
            self.t.start()

    def _run(self, spk):
        with spk.player(samplerate=self.out_sr, channels=self.ch, blocksize=1024) as p:
            while True:
                item = self.q.get()
                if item is None or self._abort.is_set():
                    if item is not None:
                        self.gate.end()
                        continue  # drop what was queued before / during abort(); the None sentinel follows
                    return
                try:
                    data = np.repeat(item.reshape(-1, 1), self.ch, axis=1)
                    step = max(1, int(self.out_sr * PLAY_BLOCK_S))
                    for i in range(0, len(data), step):
                        if self._abort.is_set():  # #427: leave a long piece within one block on Ctrl+C
                            break
                        p.play(data[i:i + step])
                finally:
                    self.gate.end()

    def push(self, audio: np.ndarray, sr: int) -> None:
        if self.enabled:
            from .logic import resample

            self.gate.start()
            self.q.put(resample(audio, sr, self.out_sr))

    def abort(self) -> None:
        """#427 (Ctrl+C): mute, drop the queued audio and let the playback thread leave the output stream."""
        self.enabled = False
        self._abort.set()
        if getattr(self, "t", None) is not None:
            self.q.put(None)  # the thread drops anything queued before it (gate released per item)

    def close(self, timeout: float = 60) -> None:
        """Play what is queued, then stop the playback thread. After abort() it only waits for that thread (#427:
        a playback thread still inside the PulseAudio stream at interpreter exit could hang the process)."""
        t = getattr(self, "t", None)
        if t is None:
            return
        if self.enabled:
            self.q.put(None)
        t.join(timeout if self.enabled else min(timeout, ABORT_JOIN_S))
        if t.is_alive():
            print("tlwu: audio output did not stop in time", file=sys.stderr, flush=True)
