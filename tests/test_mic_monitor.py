from types import SimpleNamespace as NS

from tinglongwu.capture.mic import MicSource


class FakeSC:
    def all_microphones(self, include_loopback=False):
        ms = [NS(name="Family 17h HD Audio Controller"), NS(name="RU-Mic")]
        if include_loopback:
            ms.append(NS(name="Monitor of Family 17h HD Audio Controller"))
        return ms

    def default_microphone(self):
        return NS(name="default")


def _pick(dev):
    return MicSource(device=dev)._pick_microphone(FakeSC())


def test_monitor_name_resolves_with_loopback():
    assert _pick("Monitor of Family 17h").name.startswith("Monitor of")
    assert _pick("monitor of family 17h").name.startswith("Monitor of")


def test_plain_name_unchanged_no_loopback():
    assert _pick("ru-mic").name == "RU-Mic"
    import pytest
    with pytest.raises(RuntimeError):
        _pick("tlwu_mic_nonexistent")
