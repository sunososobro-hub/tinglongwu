import sys

import numpy as np

from ._soundcard import SoundcardSource, find_by_name
from .wasapi import SilenceWatch


class MicSource(SoundcardSource):
    """Default (or --device substring) microphone. Groundwork for the mic -> TTS roadmap."""

    follow_default = True  # #1070: without --device, follow the Windows default mic mid-call (owner: change one place)
    silent_key = "listen_silent_mic"  # #1544: i18n key of the "no sound for 10 s" hint (names the input, offers the switch)

    def __init__(self, device: str | None = None):
        super().__init__(device)
        self.on_sound = None  # called once when sound returns after on_silent fired
        self.on_silent = None  # called with the list of listened input names after ~10 s of digital silence
        self.listening: list[str] = [device] if device else []

    def _pick_microphone(self, sc):
        if self.device:
            # "Monitor of ..." (PulseAudio monitor, allowed upstream via allow_monitor) is only listed with loopback
            mon = self.device.strip().lower().startswith("monitor of")
            mic = find_by_name(sc, self.device, include_loopback=mon)
        else:
            mic = sc.default_microphone()
        return mic

    def _opened(self, mic) -> None:
        name = getattr(mic, "name", None)
        if name:
            self.listening = [name]
        self._watch = SilenceWatch()  # silence counts contiguous audio of the input that is open now
        # #1544: one line per successful (re)open, so the app log says which input a session listens to
        print(f"tlwu: listen source = mic, device = {name or '?'}", file=sys.stderr, flush=True)

    def frames(self):
        self._watch = SilenceWatch()
        for blk in super().frames():
            watch = self._watch
            was_fired = watch._fired
            if self.on_silent is not None and watch.update(float(np.abs(blk).max()) if len(blk) else 0.0):
                try:
                    self.on_silent(list(self.listening))
                except Exception:  # noqa: BLE001 - a hint must never stop the capture
                    pass
            elif was_fired and not watch._fired and self.on_sound is not None:
                try:
                    self.on_sound()
                except Exception:  # noqa: BLE001
                    pass
            yield blk
