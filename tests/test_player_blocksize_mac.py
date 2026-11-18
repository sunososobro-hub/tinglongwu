"""#1555: on macOS the Player must open CoreAudio with a block size the device accepts (max 512), or nothing plays."""
import sys
import types

import numpy as np

from tinglongwu.reverse import player as player_mod
from tinglongwu.reverse.logic import resample
from tinglongwu.reverse.player import Player


class _FakeCoreAudioSpeaker:
    """Mimics soundcard/coreaudio.py: blocksize above the frames-per-slice limit raises TypeError."""

    def __init__(self, name="MacBook Pro Speakers", limit=512):
        self.name, self.limit, self.played, self.opened_with = name, limit, [], None

    def player(self, samplerate, channels, blocksize=None):
        self.opened_with = blocksize
        if blocksize is not None and not 15 <= blocksize <= self.limit:
            raise TypeError(f"blocksize must be between 15.0 and {self.limit}")
        spk = self

        class _P:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def play(self, data):
                spk.played.append(np.asarray(data))

        return _P()


def _install(monkeypatch, spk):
    resample(np.zeros(240, dtype=np.float32), 24000, 48000)  # import the resampler before faking darwin
    sc = types.SimpleNamespace(all_speakers=lambda: [spk], default_speaker=lambda: spk)
    monkeypatch.setitem(sys.modules, "soundcard", sc)
    monkeypatch.setattr("tinglongwu.capture._soundcard.patch_mixformat", lambda: None)
    monkeypatch.setattr(player_mod.sys, "platform", "darwin")
    monkeypatch.setattr("tinglongwu.reverse.vmic.loop_prone_output", lambda name: False)


def test_output_blocksize_per_platform():
    from tinglongwu.reverse.player import output_blocksize

    assert output_blocksize("darwin") is None
    assert output_blocksize("win32") == 1024
    assert output_blocksize("linux") == 1024


def test_mac_player_plays_all_audio_on_coreaudio_limited_device(monkeypatch):
    spk = _FakeCoreAudioSpeaker()
    _install(monkeypatch, spk)
    p = Player(spk.name, out_sr=48000, enabled=True)
    p.push(np.zeros(2400, dtype=np.float32), 24000)  # 0.1 s at 24 kHz
    p.close(timeout=5)
    assert not p.t.is_alive()
    assert spk.opened_with is None
    frames = sum(len(d) for d in spk.played)
    assert frames == 4800  # 0.1 s at 48 kHz
    assert all(d.shape[1] == 2 for d in spk.played)  # macOS sends identical L/R
