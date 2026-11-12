import numpy as np

from tinglongwu.asr import Transcript
from tinglongwu.reverse.live import is_source_zh, recognize_utterances


class FakeAsr:
    def __init__(self, texts):
        self.texts = iter(texts)

    def transcribe(self, seg):
        return Transcript(next(self.texts), "zh")


def test_is_source_zh():
    assert is_source_zh("你好，我們開始吧")
    assert is_source_zh("我用 Python 寫的")
    assert is_source_zh("123")
    assert not is_source_zh("Can you send NDAs to all of our")


def test_english_leak_dropped_from_speak_side():
    seg = np.full(16000, 0.1, dtype=np.float32)
    asr = FakeAsr(["Can you send NDAs to all of our", "請把保密協議寄給大家"])
    assert list(recognize_utterances([seg, seg], asr)) == ["請把保密協議寄給大家"]
