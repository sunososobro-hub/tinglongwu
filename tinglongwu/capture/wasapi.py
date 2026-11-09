from ._soundcard import SoundcardSource, find_by_name


class WasapiLoopbackSource(SoundcardSource):
    """Windows: WASAPI loopback of the default speakers (or --device substring)."""

    def _pick_microphone(self, sc):
        if self.device:
            return find_by_name(sc, self.device)
        return sc.get_microphone(sc.default_speaker().name, include_loopback=True)
