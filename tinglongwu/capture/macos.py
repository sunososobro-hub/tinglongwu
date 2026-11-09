from ._soundcard import SoundcardSource, find_by_name


class MacLoopbackSource(SoundcardSource):
    """macOS: capture a loopback virtual device (default: name contains 'BlackHole').

    Route system audio to a Multi-Output Device that includes BlackHole 2ch.
    Future option: ScreenCaptureKit audio capture (macOS 13+) would remove the
    need for a virtual device, but needs a native helper (pyobjc) - not implemented.
    """

    def _pick_microphone(self, sc):
        return find_by_name(sc, self.device or "BlackHole", include_loopback=False)
