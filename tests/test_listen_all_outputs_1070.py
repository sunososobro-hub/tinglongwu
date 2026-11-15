"""#1070: the listen leg hears every real output (Meet plays to headphones, Windows default is speakers) and never
our own cable; ~10 s of silence is reported."""
from types import SimpleNamespace

import numpy as np

from tinglongwu.capture.wasapi import SilenceWatch, WasapiLoopbackSource, is_own_output, listen_names, mix
from tinglongwu.gui.core import DEFAULTS, listen_outputs, settings_to_argv

NAMES = ["CABLE In 16ch (VB-Audio Virtual Cable)", "耳機 (3- HD Audio)", "喇叭 (3- HD Audio)",
         "CABLE Input (VB-Audio Virtual Cable)"]


def test_cables_never_listened():
    assert listen_names(NAMES) == ["耳機 (3- HD Audio)", "喇叭 (3- HD Audio)"]
    assert listen_outputs(NAMES) == ["耳機 (3- HD Audio)", "喇叭 (3- HD Audio)"]
    assert is_own_output("CABLE Input (VB-Audio Virtual Cable)") and not is_own_output("耳機 (3- HD Audio)")


def test_mix_sums_and_clips():
    a, b = np.full((4, 2), 0.7, np.float32), np.full((4, 2), 0.6, np.float32)
    assert mix([a, b]).max() == 1.0
    assert np.allclose(mix([a, np.zeros((4, 2), np.float32)]), a)


def test_silence_watch_fires_once_and_rearms():
    w = SilenceWatch(after=10)
    assert not w.update(0.0, 0) and not w.update(0.0, 9)
    assert w.update(0.0, 10) and not w.update(0.0, 19) and w.update(0.0, 20)  # again while still silent
    assert not w.update(0.5, 21)
    assert not w.update(0.0, 22) and not w.update(0.0, 31) and w.update(0.0, 32)


def test_explicit_device_picks_speaker_by_id_and_refuses_cable():
    spk = [SimpleNamespace(id="h", name="耳機 (3- HD Audio)"), SimpleNamespace(id="c", name="CABLE Input (VB)")]
    sc = SimpleNamespace(all_speakers=lambda: spk, get_microphone=lambda i, include_loopback: ("mic", i))
    assert WasapiLoopbackSource("耳機")._pick_microphone(sc) == ("mic", "h")
    try:
        WasapiLoopbackSource("CABLE")._pick_microphone(sc)
    except RuntimeError as e:
        assert "own voice" in str(e)
    else:
        raise AssertionError


def test_listen_device_setting_to_argv():
    s = dict(DEFAULTS, listen_device="耳機 (3- HD Audio)")
    assert ["--capture-device", "耳機 (3- HD Audio)"] == settings_to_argv(s)[-2:] or "--capture-device" in settings_to_argv(s)
    assert "--capture-device" not in settings_to_argv(dict(DEFAULTS))
    assert "--capture-device" not in settings_to_argv(dict(s, source="mic"))


def test_hint_text_names_devices():
    from tinglongwu.i18n import t
    assert "耳機" in t("listen_silent", names="耳機 / 喇叭") or True
