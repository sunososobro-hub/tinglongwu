"""#833: GUI speak lines are held and sent by the shared SendDecider (#832) through SendGate / VoiceSink."""
import time

from tinglongwu.gui.core import send_gate_line
from tinglongwu.reverse import sendmode
from tinglongwu.reverse.sendgate import SendGate
from tinglongwu.sink import Result, Sink


class NullSink(Sink):
    def write(self, r):
        pass

from tinglongwu.voice import VoiceSink


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def make(mode="countdown"):
    clk, out = Clock(), []
    return SendGate(out.append, clk, mode=mode), clk, out


def test_countdown_sends_after_two_seconds_with_no_input():
    g, clk, out = make()
    g.offer("你好", "hello")
    assert out == [] and g.view().state == "counting"
    clk.t += 1.9
    g.tick()
    assert out == []
    clk.t += 0.2
    g.tick()
    assert out == ["hello"] and g.view().state == "idle"
    assert sendmode.COUNTDOWN_S >= 2.0


def test_stop_discards_the_held_line():  # stop button, Space and Esc all call stop()
    g, clk, out = make()
    g.offer("你好", "hello")
    g.stop()
    clk.t += 10
    g.tick()
    assert out == [] and g.discarded == 1


def test_low_confidence_line_waits_for_confirm():
    g, clk, out = make()
    g.offer("你好", "hello", extra={"halluc": True})
    clk.t += 10
    g.tick()
    assert out == [] and g.view().state == "asking"
    g.confirm()
    assert out == ["hello"]


def test_confirm_mode_waits_and_auto_sends_at_once():
    g, clk, out = make("confirm")
    g.offer("a", "one")
    clk.t += 60
    g.tick()
    assert out == [] and g.view().state == "asking"
    g.confirm()
    assert out == ["one"]
    g2, _, out2 = make("auto")
    g2.offer("a", "two")
    assert out2 == ["two"]


def test_next_line_waits_behind_the_held_one():
    g, clk, out = make()
    g.offer("a", "one")
    g.offer("b", "two")
    g.stop()
    assert g.view().text == "two"
    clk.t += 2.5
    g.tick()
    assert out == ["two"]


def test_voice_sink_pushes_no_audio_until_released():
    spoken = []
    clk = Clock()
    vs = VoiceSink(NullSink(), spoken.append)
    vs.gate = SendGate(vs.enqueue, clk, mode="countdown")
    vs.write(Result("zh", "你好", None, "hello"))
    vs.write(Result("zh", "再見", None, "bye"))
    vs.gate.stop()  # first line discarded
    time.sleep(0.1)
    assert spoken == []
    clk.t += 3
    vs.gate.tick()
    vs.close()
    assert spoken == ["bye"]


def test_clear_on_close_sends_nothing():
    spoken = []
    clk = Clock()
    vs = VoiceSink(NullSink(), spoken.append)
    vs.gate = SendGate(vs.enqueue, clk, mode="countdown")
    vs.write(Result("zh", "你好", None, "hello"))
    vs.close(drop=True)
    clk.t += 5
    vs.gate.tick()
    assert spoken == []


def test_gate_line_text():
    g, clk, _ = make()
    g.offer("你好", "hello")
    assert "hello" in send_gate_line(g.view())
    g.stop()
    assert send_gate_line(g.view()) == ""
