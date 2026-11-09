from ._soundcard import SoundcardSource, find_by_name


class MicSource(SoundcardSource):
    """Default (or --device substring) microphone. Groundwork for the mic -> TTS roadmap."""

    def _pick_microphone(self, sc):
        if self.device:
            return find_by_name(sc, self.device, include_loopback=False)
        return sc.default_microphone()
