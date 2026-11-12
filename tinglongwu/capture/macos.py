from ._soundcard import SoundcardSource, find_by_name

CAPTURE_DEVICE = "BlackHole 2ch"  # system audio; "BlackHole 16ch" is the virtual MIC for speech (reverse/vmic.py)


def pick_blackhole(names: list[str], device: str | None = None) -> str:
    """Name of the capture device: an explicit `device` substring wins; otherwise 'BlackHole 2ch', else any
    BlackHole that is not the 16ch/64ch one (those carry our own speech / are not the system-audio path)."""
    if device:
        for n in names:
            if device.casefold() in n.casefold():
                return n
        raise RuntimeError(f"No capture device matching {device!r}. Available: {list(names)}")
    for n in names:
        if CAPTURE_DEVICE.casefold() in n.casefold():
            return n
    for n in names:
        low = n.casefold()
        if "blackhole" in low and "16ch" not in low and "64ch" not in low:
            return n
    raise RuntimeError(
        f"No 'BlackHole 2ch' capture device found. Available: {list(names)}.\n"
        "Install: brew install --cask blackhole-2ch, restart the Mac, then set the system output to a "
        "Multi-Output Device (speakers + BlackHole 2ch) in Audio MIDI Setup.")


class MacLoopbackSource(SoundcardSource):
    """macOS: capture 'BlackHole 2ch' (or --capture-device substring).

    Route system audio to a Multi-Output Device that includes BlackHole 2ch. 'BlackHole 16ch' is NOT used here: it is
    the virtual microphone our own speech is played into, so listening to it would feed the speech back.
    Future option: ScreenCaptureKit audio capture (macOS 13+) would remove the
    need for a virtual device, but needs a native helper (pyobjc) - not implemented.
    """

    def _pick_microphone(self, sc):
        mics = sc.all_microphones(include_loopback=False)
        name = pick_blackhole([m.name for m in mics], self.device)
        return find_by_name(sc, name, include_loopback=False)
