"""#39: capture follows a leftover null sink as default output silently -> warn once at start."""
from types import SimpleNamespace

from tinglongwu.capture import pulse
from tinglongwu.capture.pulse import PulseMonitorSource, virtual_sink_warning, null_sink_warning, parse_sinks


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


PACTL = """Sink #0
\tName: alsa_output.pci-0000_00_1f.3.analog-stereo
\tDescription: Built-in Audio Analog Stereo
\tDriver: PipeWire
Sink #1
\tName: ru_voice
\tDescription: Whatever
\tDriver: module-null-sink.c
"""


def test_any_named_null_sink_warns_with_physical_fix():
    msg = null_sink_warning("Whatever", "ru_voice", parse_sinks(PACTL))
    assert "ru_voice" in msg
    assert "pactl set-default-sink alsa_output.pci-0000_00_1f.3.analog-stereo" in msg
    assert "Built-in Audio Analog Stereo" in msg


def test_physical_default_does_not_warn_via_pactl():
    sinks = parse_sinks(PACTL)
    assert null_sink_warning("Built-in Audio Analog Stereo", "alsa_output.pci-0000_00_1f.3.analog-stereo", sinks) is None


def test_pick_microphone_uses_pactl_and_does_not_change_default(monkeypatch, capsys):
    monkeypatch.setattr(pulse, "_list_sinks", lambda: parse_sinks(PACTL))
    sc = FakeSc("Whatever", "ru_voice")
    PulseMonitorSource()._pick_microphone(sc)
    assert "set-default-sink alsa_output" in capsys.readouterr().err
    assert sc.asked == ("Whatever", True)
