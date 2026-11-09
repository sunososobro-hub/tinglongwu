from ._soundcard import SoundcardSource, find_by_name


class PulseMonitorSource(SoundcardSource):
    """Linux PipeWire/PulseAudio: monitor of the default sink (or --device substring).

    List candidates with `pactl list sources short` (names ending in .monitor).
    """

    def _pick_microphone(self, sc):
        if self.device:
            return find_by_name(sc, self.device)
        return sc.get_microphone(sc.default_speaker().name, include_loopback=True)
