"""Default loopback is looked up by speaker id: a headset mic sharing the speaker's name made the
name lookup return the (non-loopback) mic, and soundcard then failed with a bare AssertionError."""
from types import SimpleNamespace

from tinglongwu.capture.wasapi import WasapiLoopbackSource


def test_default_loopback_looked_up_by_id():
    speaker = SimpleNamespace(id="{spk-id}", name="Headset (USBC Headset)")
    calls = []
    sc = SimpleNamespace(default_speaker=lambda: speaker,
                         get_microphone=lambda key, include_loopback: calls.append((key, include_loopback)) or "mic")
    assert WasapiLoopbackSource()._pick_microphone(sc) == "mic"
    assert calls == [("{spk-id}", True)]
