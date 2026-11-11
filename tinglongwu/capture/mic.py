from ._soundcard import SoundcardSource, find_by_name


class MicSource(SoundcardSource):
    """Default (or --device substring) microphone. Groundwork for the mic -> TTS roadmap."""

    def _pick_microphone(self, sc):
        if self.device:
            # Linux dev monitor (--allow-monitor path): PulseAudio monitor sources are only listed with loopback on.
            # check_mic_feedback still rejects tlwu_* monitors; every other name keeps include_loopback=False.
            monitor = self.device.strip().lower().startswith("monitor of")
            return find_by_name(sc, self.device, include_loopback=monitor)
        return sc.default_microphone()
