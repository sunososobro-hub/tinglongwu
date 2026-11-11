"""#134: speak mode reverse direction - I speak English, the other side hears Chinese (proto --from en)."""
import json
import os
import struct
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from tests.test_reverse_en import _parse
from tinglongwu.reverse import proto
from tinglongwu.reverse.confirm import back_translate
from tinglongwu.reverse.live import is_source_lang, recognize_utterances
from tinglongwu.reverse.logic import translate_ru

WORKER = Path(proto.__file__).with_name("tts_worker.py")


def test_from_en_implies_to_zh_kokoro():
    a = _parse(["--from", "en", "--tts-engine", "kokoro"])
    assert (a.src, a.to, a.speaker) == ("en", "zh", 0)
    assert _parse(["--from", "en", "--no-tts"]).to == "zh"
    assert _parse([]).src == "zh" and _parse([]).to == "ru"  # default unchanged


@pytest.mark.parametrize("argv", [["--from", "en"],  # vosk has no Chinese voice
                                  ["--from", "en", "--to", "ru", "--tts-engine", "kokoro"],
                                  ["--from", "en", "--to", "follow", "--no-tts"],
                                  ["--to", "zh", "--tts-engine", "kokoro"]])  # zh -> zh makes no sense
def test_from_en_bad_combinations(argv):
    with pytest.raises(SystemExit):
        _parse(argv)


def test_source_filter_english():
    assert is_source_lang("Can you send the NDA to everyone?", "en")
    assert is_source_lang("OK, 3 pm.", "en")
    assert not is_source_lang("請把保密協議寄給大家", "en")
    assert not is_source_lang("привет всем", "en")
    assert is_source_lang("請把保密協議寄給大家", "zh") and not is_source_lang("hello", "zh")


class _Asr:
    def __init__(self, texts):
        self.texts = iter(texts)

    def transcribe(self, seg):
        from types import SimpleNamespace

        return SimpleNamespace(text=next(self.texts), no_speech=0.0)


def test_recognize_drops_leaked_chinese_when_speaking_english():
    seg = np.full(16000, 0.1, np.float32)
    out = recognize_utterances([seg, seg], _Asr(["好的，我們開始吧", "Let's get started with the budget."]), src="en")
    assert list(out) == ["Let's get started with the budget."]


def test_translate_ru_keeps_chinese_output():
    calls = []

    def tr(x):
        calls.append(x)
        return "我們開始吧。"

    assert translate_ru(tr, "Let's start.", "zh") == ("我們開始吧。", 0) and calls == ["Let's start."]


class _MT:
    def __init__(self):
        self.calls, self.history, self._pair = [], ["ctx"], ("en", "zh")

    def translate(self, text, src, tgt):
        from types import SimpleNamespace

        self.calls.append((src, tgt))
        return SimpleNamespace(text={"zh": "你好。", "en": "Hello."}[tgt])


def test_back_translation_goes_to_english():
    mt = _MT()
    assert back_translate(mt, "你好。", "zh", "en") == "Hello." and mt.calls == [("zh", "en")]
    assert mt.history == ["ctx"]


def test_auto_live_translates_en_to_zh_and_speaks():
    class TTS:
        def speak(self, s):
            yield {"t": "chunk", "sr": 24000}, np.zeros(10, np.float32).tobytes()
            yield {"t": "done"}, None

    class Player:
        pushed = []

        def push(self, audio, sr):
            self.pushed.append(sr)

    mt, res = _MT(), {"items": []}
    proto._run_auto_live(iter([(1, "mic", "Hello.")]), mt, TTS(), Player(), res, "zh", src="en")
    assert mt.calls == [("en", "zh")]
    it = res["items"][0]
    assert (it["en"], it["zh"], it["decision"]) == ("Hello.", "你好。", "sent")


def test_build_translator_en_prefers_hymt_then_opus(monkeypatch, tmp_path):
    import tinglongwu.translate as tr
    from tinglongwu.hymt import JaRouter

    loaded = []
    monkeypatch.setattr(tr.PivotTranslator, "preload", lambda self, s, t: loaded.append((s, t)))
    # no Hy-MT2 GGUF: OPUS en-zh / zh-en; auto never picks Qwen for --from en, even on CUDA
    mt = proto.build_translator("auto", "cuda", tmp_path, "qwen3-4b", "zh", src="en")
    assert isinstance(mt, JaRouter) and mt.all_pairs and mt.hy is None
    assert loaded == [("en", "zh"), ("zh", "en")]

    gguf = tmp_path / "hymt2-1.8b" / "Hy-MT2-1.8B-Q4_K_M.gguf"
    gguf.parent.mkdir()
    gguf.write_bytes(b"x")
    monkeypatch.setattr("tinglongwu.hymt.HyMtTranslator.preload", lambda self, s="", t="": None)
    loaded.clear()
    mt = proto.build_translator("auto", "cpu", tmp_path, "qwen3-4b", "zh", src="en")
    assert mt.hy is not None and mt._use_hy("en", "zh") and mt._use_hy("zh", "en") and loaded == []


def _read(proc):
    h = json.loads(proc.stdout.readline())
    return h, (proc.stdout.read(h["n"]) if "n" in h else None)


def test_worker_speaks_chinese_with_zh_voice_and_misaki_phonemes(tmp_path):
    """Run the real tts_worker with stub kokoro_onnx / misaki packages: a lang "zh" request must go through
    misaki.zh phonemes and a Kokoro Mandarin voice."""
    stubs = tmp_path / "stubs"
    (stubs / "misaki").mkdir(parents=True)
    (stubs / "misaki" / "__init__.py").write_text("")
    (stubs / "misaki" / "zh.py").write_text(
        "class ZHG2P:\n    def __call__(self, text):\n        return ('PH[' + text + ']', None)\n")
    log = tmp_path / "create.json"
    (stubs / "kokoro_onnx.py").write_text(
        "import json, os\n"
        "class Kokoro:\n"
        "    def __init__(self, onnx, voices): pass\n"
        "    def get_voices(self): return ['af_heart', 'am_michael', 'zf_xiaoxiao', 'zm_yunxi']\n"
        "    def create(self, voice, speed, **kw):\n"
        f"        open({str(log)!r}, 'a').write(json.dumps(dict(voice=voice, **kw)) + '\\n')\n"
        "        return [0.0] * 240, 24000\n")
    mdir = tmp_path / "kokoro"
    mdir.mkdir()
    for f in ("kokoro-v1.0.int8.onnx", "voices-v1.0.bin"):
        (mdir / f).write_bytes(b"x")
    env = {**os.environ, "PYTHONPATH": str(stubs), "PYTHONUTF8": "1"}
    p = subprocess.Popen([sys.executable, str(WORKER), "--engine", "kokoro", "--model", str(mdir), "--speaker", "1"],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env)
    try:
        assert _read(p)[0]["t"] == "ready"
        for req in ({"id": 1, "text": "我們開始吧", "lang": "zh", "speaker": 1},
                    {"id": 2, "text": "Hello", "lang": "en", "speaker": 1}):
            p.stdin.write((json.dumps(req) + "\n").encode())
            p.stdin.flush()
            h, data = _read(p)
            assert h["t"] == "chunk" and len(data) == 240 * struct.calcsize("f")
            assert _read(p)[0]["t"] == "done"
        p.stdin.write(b'{"cmd": "quit"}\n')
        p.stdin.flush()
        assert p.wait(15) == 0
    finally:
        p.kill()
    zh, en = [json.loads(x) for x in log.read_text().splitlines()]
    assert zh == {"voice": "zm_yunxi", "text": "PH[我們開始吧]", "is_phonemes": True}
    assert en == {"voice": "af_heart", "text": "Hello", "lang": "en-us"}
