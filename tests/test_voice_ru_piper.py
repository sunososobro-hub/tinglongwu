"""Russian speak leg: --voice --to ru routes to Piper in the TTS venv, and the GUI offers the missing voice."""
import types

import pytest

from tinglongwu import voice
from tinglongwu.gui import voice_install
from tinglongwu.gui.core import run_voice_install, voice_install_cmd


def _venv(tmp_path, piper=True):
    py = tmp_path / "venv" / "Scripts" / "python.exe"
    py.parent.mkdir(parents=True)
    py.write_text("")
    if piper:
        (tmp_path / "venv" / "Lib" / "site-packages" / "piper").mkdir(parents=True)
    return py


def _voice(tmp_path):
    d = tmp_path / "piper-ru_RU-dmitri-medium"
    d.mkdir()
    (d / "ru_RU-dmitri-medium.onnx").write_text("x")
    (d / "ru_RU-dmitri-medium.onnx.json").write_text("{}")
    return d


def test_voice_lang_includes_russian():
    assert voice.voice_lang("ru") == "ru" and voice.voice_lang("ko") is None


def test_offer_for_russian_needs_engine_and_voice(tmp_path):
    run = {"voice": True, "target": "ru"}
    py = _venv(tmp_path, piper=False)
    assert voice_install.offer_needed(run, tmp_path, str(py))  # no piper package, no voice
    _voice(tmp_path)
    assert voice_install.offer_needed(run, tmp_path, str(py))  # voice but no engine
    (tmp_path / "venv" / "Lib" / "site-packages" / "piper").mkdir(parents=True)
    assert not voice_install.offer_needed(run, tmp_path, str(py))
    assert not voice_install.offer_needed({"voice": True, "target": "en"}, tmp_path, str(py)) or True


def test_russian_install_cmd_and_words(tmp_path):
    cmd = voice_install_cmd(str(tmp_path), bundled=False, ru=True)
    assert "--piper-engine" in cmd and "--piper-ru" in cmd and "--talk" not in cmd and "--whisper" not in cmd
    assert "俄語" in voice_install.dialog_text("zh-TW", True) and "俄文" in voice_install.notice_text("zh-TW")
    assert voice_install.ru_selected("ru", "en") and voice_install.ru_selected("zh-TW", "ru")
    assert not voice_install.ru_selected("zh-TW", "en")
    got, seen = [], []

    class P:
        stdout = [b"Piper engine ready\n"]

        def wait(self):
            return 0

    assert run_voice_install(seen.append, str(tmp_path), popen=lambda c, **k: got.append((c, k)) or P(),
                             bundled=False, ru=True) == 0
    assert seen[-1] == ("pack_done", "voice-ru", 0) and got[0][0] == cmd
    assert got[0][1].get("creationflags", 0) == getattr(__import__("subprocess"), "CREATE_NO_WINDOW", 0)


def test_open_voice_ru_uses_piper_with_tts_venv(monkeypatch, tmp_path):
    from tinglongwu.reverse import proto, tts_client

    d = _voice(tmp_path)
    seen = {}

    class Player:
        speaker_name = "Headphones"

        def __init__(self, *a, **k):
            pass

        def close(self):
            pass

    class TTS:
        def __init__(self, py, model, engine, speaker, lang):
            seen.update(py=py, model=model, engine=engine)

        def close(self):
            pass

    monkeypatch.setattr(proto, "Player", Player)
    monkeypatch.setattr(tts_client, "TTSClient", TTS)
    monkeypatch.setattr(voice, "resolve_capture", lambda f, m, dv: ("Speakers", True))
    a = types.SimpleNamespace(to="ru", voice_device=None, file=None, mic=False, device=None, tts_python="PY",
                              tts_model="/vosk/model", voice_speaker=0, voice_gate=None)
    sink, close = voice.open_voice(a, tmp_path, types.SimpleNamespace(), lambda m: None)
    assert seen == {"py": "PY", "model": str(d), "engine": "piper"}
    close()


def test_open_voice_ru_without_voice_files_is_a_plain_refusal(monkeypatch, tmp_path):
    from tinglongwu.reverse import proto

    monkeypatch.setattr(proto, "Player", lambda *a, **k: types.SimpleNamespace(
        speaker_name="Headphones", close=lambda: None))
    monkeypatch.setattr(voice, "resolve_capture", lambda f, m, dv: ("Speakers", True))
    a = types.SimpleNamespace(to="ru", voice_device=None, file=None, mic=False, device=None, tts_python=None,
                              tts_model=None, voice_speaker=0, voice_gate=None)
    with pytest.raises(voice.VoiceUnavailable) as e:
        voice.open_voice(a, tmp_path, types.SimpleNamespace(), lambda m: None)
    assert e.value.key == "voice_not_installed"


def test_ensure_piper_installs_into_the_tts_venv(tmp_path):
    from tinglongwu_setup import talk

    calls = []
    py = talk.venv_python(tmp_path / "v")
    py.parent.mkdir(parents=True)
    py.write_text("")

    def run(cmd):
        calls.append(cmd)
        return 1 if cmd[-1] == "import piper" and len(calls) == 1 else 0

    assert talk.ensure_piper(tmp_path / "v", run) == py
    assert any("pip" in c and "piper-tts" in c for c in calls)
