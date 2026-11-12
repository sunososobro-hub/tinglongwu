from ._soundcard import SoundcardSource, find_by_name


class MicSource(SoundcardSource):
    """Default (or --device substring) microphone. Groundwork for the mic -> TTS roadmap."""

    def _pick_microphone(self, sc):
        if self.device:
            # "Monitor of ..." (PulseAudio monitor, allowed upstream via allow_monitor) is only listed with loopback
            mon = self.device.strip().lower().startswith("monitor of")
            return find_by_name(sc, self.device, include_loopback=mon)
        return sc.default_microphone()
