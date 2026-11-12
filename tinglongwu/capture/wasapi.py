from ._soundcard import SoundcardSource, find_by_name


class WasapiLoopbackSource(SoundcardSource):
    """Windows: WASAPI loopback of the default speakers (or --device substring)."""

    def _pick_microphone(self, sc):
        if self.device:
            return find_by_name(sc, self.device)
        # by id: a headset mic can share the speaker name, and a name lookup then returns the mic, not the loopback
        return sc.get_microphone(sc.default_speaker().id, include_loopback=True)
