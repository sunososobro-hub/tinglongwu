"""#128: Russian speak-mode voice via Piper (dmitri/denis, CC0) in its own venv + subprocess."""
import hashlib
import json
import sys
import textwrap
from pathlib import Path

import numpy as np
import pytest

from tinglongwu.models import default_piper_ru, piper_onnx, piper_ru_dir
from tinglongwu.reverse.tts_client import TTSClient
from tinglongwu_setup import downloader as dl

from test_reverse_en import _parse

FAKE_PIPER = textwrap.dedent('''
    """Stand-in for piper-tts: same API surface as piper.PiperVoice (load / synthesize -> AudioChunk)."""
    import numpy as np


    class AudioChunk:
        def __init__(self, n):
            self.sample_rate = 22050
            self.audio_float_array = np.full(n, 0.25, dtype=np.float32)


    class PiperVoice:
        @staticmethod
        def load(model_path, config_path=None, use_cuda=False, download_dir=None, **kw):
            assert model_path.endswith(".onnx") and use_cuda is False
            return PiperVoice()

        def synthesize(self, text):
            if text == "boom":
                raise ValueError("bad text")
            return [AudioChunk(2205 * len(s.split())) for s in text.split(".") if s.strip()]
''')


def _voice(d: Path, voice="dmitri") -> Path:
    f = d / "ru" / "ru_RU" / voice / "medium" / f"ru_RU-{voice}-medium.onnx"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"onnx")
    f.with_name(f.name + ".json").write_text("{}", "utf-8")
    return d


def test_piper_onnx_needs_config(tmp_path):
    (tmp_path / "x.onnx").write_bytes(b"")
    assert piper_onnx(tmp_path) is None and piper_onnx("") is None
    (tmp_path / "x.onnx.json").write_text("{}")
    assert piper_onnx(tmp_path) == tmp_path / "x.onnx"


def test_auto_picks_piper_for_explicit_piper_model(tmp_path):
    a = _parse(["--to", "ru", "--tts-model", str(_voice(tmp_path / "p"))])
    assert (a.tts_engine, a.no_tts) == ("piper", False)


def test_auto_picks_installed_piper_without_tts_model(tmp_path):
    _voice(tmp_path / piper_ru_dir("denis"), "denis")
    assert default_piper_ru(str(tmp_path)) == tmp_path / piper_ru_dir("denis")
    a = _parse(["--to", "ru", "--models-dir", str(tmp_path)])
    assert a.tts_engine == "piper" and a.tts_model == str(tmp_path / piper_ru_dir("denis")) and not a.no_tts


def test_auto_keeps_vosk_for_vosk_model_and_explicit_engine(tmp_path):
    vosk = tmp_path / "vosk"
    vosk.mkdir()
    assert _parse(["--to", "ru", "--tts-model", str(vosk)]).tts_engine == "vosk"
    _voice(tmp_path / piper_ru_dir("dmitri"))
    a = _parse(["--to", "ru", "--tts-engine", "vosk", "--tts-model", str(vosk), "--models-dir", str(tmp_path)])
    assert a.tts_engine == "vosk"


def test_piper_rejects_other_targets_and_missing_model(tmp_path, capsys):
    with pytest.raises(SystemExit):
        _parse(["--to", "en", "--tts-engine", "piper", "--tts-model", str(_voice(tmp_path / "p"))])
    a = _parse(["--to", "ru", "--tts-engine", "piper", "--tts-model", str(tmp_path / "nope")])
    assert a.no_tts is True and a.ru_voice is False


def test_follow_uses_piper_for_russian(tmp_path):
    _voice(tmp_path / piper_ru_dir("dmitri"))
    a = _parse(["--to", "follow", "--tts-engine", "kokoro", "--tts-model", "k", "--models-dir", str(tmp_path)])
    assert a.ru_engine == "piper" and a.ru_voice is True
    assert a.vosk_model == str(tmp_path / piper_ru_dir("dmitri"))


def test_worker_speaks_through_piper(tmp_path, monkeypatch):
    """Real worker subprocess + real pipe protocol; only the piper package is a stand-in."""
    lib = tmp_path / "lib" / "piper"
    lib.mkdir(parents=True)
    (lib / "__init__.py").write_text(FAKE_PIPER, "utf-8")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "lib"))
    c = TTSClient(sys.executable, str(_voice(tmp_path / "v")), "piper")
    try:
        assert c.ready["engine"] == "piper"
        events = list(c.speak("Привет мир. Как дела у тебя"))
        chunks = [(h, d) for h, d in events if h["t"] == "chunk"]
        assert len(chunks) == 2 and all(h["sr"] == 22050 for h, _ in chunks)
        audio = np.concatenate([np.frombuffer(d, dtype=np.float32) for _, d in chunks])
        assert audio.size == 2205 * 6 and np.allclose(audio, 0.25)
        done = events[-1][0]
        assert done["t"] == "done" and done["audio_s"] == pytest.approx(0.6) and done["ttfa_s"] is not None
        assert [h["t"] for h, _ in c.speak("boom")] == ["error"]
        assert [h["t"] for h, _ in c.speak("Ещё.")][-1] == "done"  # worker survives an error
    finally:
        c.close()


def test_worker_reports_missing_model(tmp_path):
    with pytest.raises(RuntimeError, match="missing local file"):
        TTSClient(sys.executable, str(tmp_path), "piper")


def _fake_net(monkeypatch, body_for):
    """Fake HF download (#688: pinned files now go through resume.fetch): body_for(file path) -> bytes."""
    import io

    from tinglongwu_setup import resume

    class R(io.BytesIO):
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(resume, "_open_range", lambda url, start, timeout=60: R(body_for(url.split("/resolve/abc/")[1])))


def test_setup_downloads_and_pins_both_files(tmp_path, monkeypatch):
    _fake_net(monkeypatch, lambda f: f.encode())
    files = dl.PIPER_RU["denis"]
    pin = {"repo": dl.PIPER_REPO, "revision": "abc", "files": {f: hashlib.sha256(f.encode()).hexdigest() for f in files}}
    monkeypatch.setattr(dl, "load_pins", lambda: {"models": {piper_ru_dir("denis"): pin}, "argos": {}})
    assert dl.main(["--piper-ru", "denis", "--models-dir", str(tmp_path)]) == 0
    man = json.loads((tmp_path / "manifest.json").read_text("utf-8"))
    assert set(man[piper_ru_dir("denis")]["files"]) == set(files)
    assert default_piper_ru(str(tmp_path)) == tmp_path / piper_ru_dir("denis")
    assert dl.main(["--verify", "--models-dir", str(tmp_path)]) == 0


def test_setup_refuses_hash_mismatch(tmp_path, monkeypatch):
    _fake_net(monkeypatch, lambda f: b"tampered")
    pin = {"repo": dl.PIPER_REPO, "revision": "abc", "files": {f: "0" * 64 for f in dl.PIPER_RU["dmitri"]}}
    monkeypatch.setattr(dl, "load_pins", lambda: {"models": {piper_ru_dir("dmitri"): pin}, "argos": {}})
    with pytest.raises(ValueError, match="(?i)sha"):
        dl.main(["--piper-ru", "--models-dir", str(tmp_path)])


def test_only_cc0_voices_offered():
    assert set(dl.PIPER_RU) == {"dmitri", "denis"}
    assert all(piper_ru_dir(v) == dl.piper_ru_dir(v) for v in dl.PIPER_RU)


def test_tts_client_spawns_worker_without_a_window(monkeypatch):
    """#128: the TTS worker (Piper/Vosk/Kokoro venv python) must not flash a console window on Windows."""
    import io
    import subprocess

    from tinglongwu.reverse import tts_client

    seen = {}

    class FakeProc:
        stdin, stdout = io.BytesIO(), io.BytesIO(b'{"t": "ready"}\n')

        def kill(self):
            pass

    def fake_popen(cmd, **kw):
        seen.update(kw)
        return FakeProc()

    monkeypatch.setattr(tts_client.subprocess, "Popen", fake_popen)
    tts_client.TTSClient("py", "m", "piper")
    assert seen["creationflags"] == getattr(subprocess, "CREATE_NO_WINDOW", 0)
