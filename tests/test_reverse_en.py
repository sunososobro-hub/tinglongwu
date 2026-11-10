"""English output for reverse mode: --to en parsing, Kokoro voice map, kokoro worker command line."""
import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from tinglongwu.reverse import proto, tts_client
from tinglongwu.reverse.confirm import Utterance, back_translate
from tinglongwu.reverse.logic import RETRY_HINTS, translate_ru

WORKER = Path(tts_client.__file__).with_name("tts_worker.py")


def _worker_const(name):
    """Read a module-level literal from tts_worker.py without importing it (it rewires fd 1 on import)."""
    for node in ast.parse(WORKER.read_text("utf-8")).body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == name:
            return ast.literal_eval(node.value)
    raise AssertionError(name)


def _worker_kokoro_voice():
    src = WORKER.read_text("utf-8")
    tree = ast.parse(src)
    keep = [n for n in tree.body if (isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "KOKORO_VOICES")
            or (isinstance(n, ast.FunctionDef) and n.name == "kokoro_voice")]
    ns = {"sys": __import__("sys")}
    exec(compile(ast.Module(body=keep, type_ignores=[]), str(WORKER), "exec"), ns)  # noqa: S102
    return ns["kokoro_voice"]


def _parse(argv):
    """Run only the argparse + check_args part of proto._main (stops at --list-mics)."""
    import argparse

    captured = {}
    real = argparse.ArgumentParser.parse_args

    def spy(self, args=None, namespace=None):
        ns = real(self, args, namespace)
        err = proto.check_args(ns)
        if err:
            self.error(err)
        captured["a"] = ns
        return ns

    argparse.ArgumentParser.parse_args = spy
    try:
        proto._main(list(argv) + ["--list-mics"], [])
    except Exception:  # noqa: BLE001  (no audio stack needed: we only want the namespace)
        pass
    finally:
        argparse.ArgumentParser.parse_args = real
    return captured["a"]


def test_default_is_russian_vosk_speaker_4():
    a = _parse([])
    assert (a.to, a.speaker) == ("ru", 4)


def test_to_en_kokoro_defaults_speaker_0():
    a = _parse(["--to", "en", "--tts-engine", "kokoro", "--mt", "opus"])
    assert (a.to, a.tts_engine, a.speaker, a.mt) == ("en", "kokoro", 0, "opus")
    assert _parse(["--to", "en", "--tts-engine", "kokoro", "--speaker", "3"]).speaker == 3


def test_to_en_text_only_needs_no_engine():
    assert _parse(["--to", "en", "--no-tts"]).to == "en"


@pytest.mark.parametrize("argv", [["--to", "en", "--tts-engine", "vosk"], ["--to", "en"],
                                  ["--to", "ru", "--tts-engine", "kokoro"], ["--to", "de"]])
def test_bad_combinations_rejected(argv):
    with pytest.raises(SystemExit):
        _parse(argv)


def test_voice_mapping():
    assert _worker_const("KOKORO_VOICES") == ["am_michael", "af_heart", "am_adam", "af_bella", "bm_george"]
    kv, warns = _worker_kokoro_voice(), []
    for i, name in enumerate(_worker_const("KOKORO_VOICES")):
        assert kv(i, None, warns.append) == name
    assert not warns


def test_voice_fallback_warns():
    kv, warns = _worker_kokoro_voice(), []
    assert kv(1, {"am_michael"}, warns.append) == "am_michael"  # af_heart missing from voices.bin
    assert kv(9, None, warns.append) == "am_michael"  # unknown id
    assert kv(-1, None, warns.append) == "am_michael"
    assert len(warns) == 3 and all("kokoro" in w for w in warns)


class FakeProc:
    def __init__(self):
        self.stdin = SimpleNamespace(write=self._w, flush=lambda: None, close=lambda: None)
        self.written = []
        self.stdout = SimpleNamespace(readline=lambda: b'{"t": "ready"}\n')

    def _w(self, b):
        self.written.append(b)

    def poll(self):
        return 0


def test_kokoro_command_line_and_env(monkeypatch):
    seen = {}

    def popen(cmd, **kw):
        seen["cmd"], seen["kw"] = cmd, kw
        return FakeProc()

    monkeypatch.setattr(tts_client.subprocess, "Popen", popen)
    c = tts_client.TTSClient("/k/py", "/models/kokoro", "", engine="kokoro", speaker=2)
    assert seen["cmd"] == ["/k/py", str(WORKER), "--engine", "kokoro", "--model", "/models/kokoro",
                           "--speaker", "2"]
    assert seen["kw"]["env"]["HF_HUB_OFFLINE"] == "1"
    assert c.engine == "kokoro"
    # speaker travels with every request so v0-v4 takes effect on the next sentence
    c.speaker = 4
    c.proc.stdout = SimpleNamespace(readline=lambda: b'{"t": "done"}\n', read=lambda n: b"")
    list(c.speak("Hello."))
    assert b'"speaker": 4' in c.proc.written[-1] and b"Hello." in c.proc.written[-1]


def test_main_process_never_imports_kokoro():
    """kokoro-onnx bundles GPL espeak: only the worker (own venv) may import it; never a dependency here."""
    root = WORKER.parents[2]
    for f in (root / "tinglongwu").rglob("*.py"):
        if f != WORKER:
            assert "import kokoro" not in f.read_text("utf-8") and "from kokoro" not in f.read_text("utf-8"), f
    for name in ("requirements.txt", "requirements-gpu.txt", "requirements-qwen.txt", "pyproject.toml"):
        assert "kokoro" not in (root / name).read_text("utf-8").lower(), name


def test_translate_hint_and_back_translation_use_target_language():
    seen = []

    def tr(x):
        seen.append(x)
        return "好" if len(seen) == 1 else "Hello."

    out, retries = translate_ru(tr, "你好", "en")
    assert (out, retries) == ("Hello.", 1) and seen[1].endswith(RETRY_HINTS["en"])
    calls = []
    mt = SimpleNamespace(history=[], _pair=None,
                         translate=lambda t, s, d: calls.append((s, d)) or SimpleNamespace(text="你好"))
    assert back_translate(mt, "Hello.", "en") == "你好" and calls == [("en", "zh")]
    assert Utterance(1, "你好", "Hello.", lang="en").lang == "en"
