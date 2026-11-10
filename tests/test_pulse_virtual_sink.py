"""#39: capture follows a leftover null sink as default output silently -> warn once at start."""
from types import SimpleNamespace

from tinglongwu.capture.pulse import PulseMonitorSource, virtual_sink_warning


class FakeSc:
    def __init__(self, name, sink_id):
        self.spk = SimpleNamespace(name=name, id=sink_id)
        self.asked = None

    def default_speaker(self):
        return self.spk

    def get_microphone(self, name, include_loopback=False):
        self.asked = (name, include_loopback)
        return "mic"


def test_leftover_tlwu_null_sink_warns(capsys):
    sc = FakeSc("tlwu_sink", "tlwu_sink")
    assert PulseMonitorSource()._pick_microphone(sc) == "mic"
    assert sc.asked == ("tlwu_sink", True)
    err = capsys.readouterr().err
    assert "tlwu_sink" in err and "set-default-sink" in err
    assert len(err.strip().splitlines()) == 1


def test_generic_null_output_warns():
    assert virtual_sink_warning("Null Output", "null")
    assert virtual_sink_warning("Speakers", "auto_null")


def test_real_speaker_is_silent(capsys):
    sc = FakeSc("Built-in Audio Analog Stereo", "alsa_output.pci-0000_00_1f.3.analog-stereo")
    PulseMonitorSource()._pick_microphone(sc)
    assert capsys.readouterr().err == ""
    assert virtual_sink_warning("HDA Intel PCH Headphones", None) is None
