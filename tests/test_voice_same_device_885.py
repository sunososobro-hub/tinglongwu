"""#885: read-aloud on the captured device never blocks listening and never shows CLI flags in the GUI."""
from __future__ import annotations

import re

import numpy as np
import pytest

from tinglongwu import cli, i18n, voice
from tinglongwu.gui.core import gui_error_text
from tinglongwu.sink import Result, Sink

SPEAKERS = "喇叭 (3- High Definition Audio Device)"
ZH_LINE = "朗讀已關閉：會被自己再錄進去。請插耳機，並在「朗讀翻譯到」選耳機。"


class _Lines(Sink):
    def __init__(self):
        self.lines = []

    def write(self, r):
        self.lines.append(r.target)

    def write_source(self, src, original):
        pass


class _Player:
    def __init__(self, device=None, sr=48000):
        self.speaker_name = device or SPEAKERS  # None = default output = the captured speakers
        self.closed = False

    def close(self):
        self.closed = True


class _Tts:
    def __init__(self, *a):
        pass

    def speak(self, text, lang=None):
        yield {"t": "chunk", "sr": 24000}, np.zeros(240, np.float32).tobytes()
        yield {"t": "done"}, None

    def close(self):
        pass


@pytest.fixture
def zh(monkeypatch):
    monkeypatch.setattr(i18n, "_override", "zh-TW")
    yield
    monkeypatch.setattr(i18n, "_override", None)


@pytest.fixture
def same_device(monkeypatch):
    from tinglongwu.reverse import proto, tts_client

    players = []
    monkeypatch.setattr(proto, "Player", lambda *a, **k: players.append(_Player(*a, **k)) or players[-1])
    monkeypatch.setattr(tts_client, "TTSClient", _Tts)
    monkeypatch.setattr(voice, "resolve_capture", lambda file, mic, device: (SPEAKERS, True))
    return players


def _no_cli_text(msg: str) -> None:
    assert "--" not in msg and "'" not in msg and not msg.isascii(), msg


def _run(monkeypatch, tmp_path, notice):
    """cli.run_session as the GUI calls it (notice set), with the real open_voice and fake models."""
    from tinglongwu import asr as asr_mod
    from tinglongwu import device as device_mod
    from tinglongwu import minspec
    from tinglongwu import pipeline as pipeline_mod

    class _A:
        device, live = "cpu", False

    class _T:
        def preload(self, *a):
            pass

    seen = {}

    def fake_run(source, seg, asr, mt, sink, *a, **k):
        seen["sink"] = sink
        sink.write(Result("en", "hello", None, "你好"))

    monkeypatch.setattr(minspec, "check_avx2", lambda force=False: None)
    monkeypatch.setattr(device_mod, "resolve_device", lambda compute, warn=None: "cpu")
    monkeypatch.setattr(asr_mod, "FasterWhisperRecognizer", lambda *a, **k: _A())
    monkeypatch.setattr(cli, "make_translator", lambda *a, **k: _T())
    monkeypatch.setattr(cli, "select_backend", lambda *a, **k: object())
    monkeypatch.setattr(pipeline_mod, "run", fake_run)
    monkeypatch.setattr(cli, "announce_ready", lambda *a, **k: seen.update(ready=True))
    sink = _Lines()
    cli.run_session(cli.parse_args(["--voice", "--to", "zh", "--file", str(tmp_path / "in.wav"), "--model", "tiny",
                                    "--lang", "en", "--models-dir", str(tmp_path)]), sink=sink, notice=notice)
    return seen, sink


def test_voice_same_device_gui_run_starts_without_voice_and_one_plain_line(zh, same_device, monkeypatch, tmp_path):
    notices = []
    seen, sink = _run(monkeypatch, tmp_path, notices.append)
    assert seen.get("ready") and sink.lines == ["你好"]  # listening started, subtitles flow
    assert seen["sink"] is sink  # no VoiceSink: read-aloud is off for this run
    assert same_device[0].closed  # the refused player is released
    voice_notes = [n for n in notices if n.startswith("朗讀")]  # (a CPU-fallback notice may come first)
    assert voice_notes == [ZH_LINE]
    _no_cli_text(voice_notes[0])


def test_voice_same_device_cli_still_refuses_with_the_flag_hint(same_device, tmp_path):
    with pytest.raises(RuntimeError, match="--voice-device NAME") as e:
        voice.open_voice(cli.parse_args(["--voice"]), tmp_path, _Lines(), lambda m: None)
    assert "being captured" in str(e.value)  # voice_output_problem check unchanged


@pytest.mark.parametrize("argv, key", [
    (["--voice"], "voice_same_device"),
    (["--voice", "--to", "ru", "--voice-device", "Headphones"], "voice_lang_unsupported"),
])
def test_voice_same_device_every_voice_refusal_has_a_localized_line(zh, same_device, tmp_path, argv, key):
    with pytest.raises(voice.VoiceUnavailable) as e:
        voice.open_voice(cli.parse_args(argv), tmp_path, _Lines(), lambda m: None)
    assert e.value.key == key and gui_error_text(e.value) == e.value.user
    _no_cli_text(e.value.user)


def test_voice_same_device_missing_output_and_missing_kokoro_are_localized(zh, monkeypatch, tmp_path):
    from tinglongwu.reverse import proto, tts_client

    def no_device(*a, **k):
        raise LookupError("no output device matching 'Headphones'")

    monkeypatch.setattr(proto, "Player", no_device)
    with pytest.raises(voice.VoiceUnavailable) as e:
        voice.open_voice(cli.parse_args(["--voice", "--voice-device", "Headphones"]), tmp_path, _Lines(), print)
    assert e.value.key == "voice_device_missing" and str(e.value).startswith("--voice-device")
    _no_cli_text(e.value.user)

    monkeypatch.setattr(proto, "Player", lambda *a, **k: _Player("Headphones (USB)"))
    monkeypatch.setattr(voice, "resolve_capture", lambda file, mic, device: (SPEAKERS, True))

    def no_kokoro(*a):
        raise OSError("python not found")

    monkeypatch.setattr(tts_client, "TTSClient", no_kokoro)
    with pytest.raises(voice.VoiceUnavailable) as e:
        voice.open_voice(cli.parse_args(["--voice"]), tmp_path, _Lines(), print)
    assert e.value.key == "voice_not_installed" and "tlwu-setup --talk" in str(e.value)
    _no_cli_text(e.value.user)


def test_voice_same_device_mid_run_mute_reason_is_localized(zh):
    """Voice mute path: the capture reopens on the cable the voice plays into (#710 guard)."""
    sink = voice.VoiceSink(_Lines(), lambda text: None)
    offs = []
    guard = voice.cable_guard(sink, "CABLE Input (VB-Audio Virtual Cable)", lambda m: None, offs.append)
    guard("CABLE Output (VB-Audio Virtual Cable)")
    sink.close(timeout=1)
    assert sink.muted and offs == ["朗讀已關閉：會被自己再錄進去。字幕繼續"]
    _no_cli_text(offs[0])


@pytest.mark.parametrize("err", [
    RuntimeError("No capture device matching 'Stereo Mix'. Available: ['Mic']"),  # capture failure
    RuntimeError("cannot read --glossary: [Errno 2] No such file"),  # start failure with a flag
    RuntimeError("No loopback capture backend for platform 'sunos5'; use --file"),
    OSError("[WinError 5] Access is denied"),  # not a RuntimeError: type name + raw text never shown
])
def test_voice_same_device_gui_error_paths_are_localized_in_zh(zh, err):
    msg = i18n.t("gui_error", msg=gui_error_text(err))
    _no_cli_text(msg)
    assert not re.search(r"\b[A-Za-z]{3,}\b", msg), msg  # no English words in a zh-TW UI


def test_voice_same_device_gui_error_keeps_plain_messages(monkeypatch):
    monkeypatch.setattr(i18n, "_override", "en")
    assert gui_error_text(RuntimeError("No capture device matching 'Stereo Mix'.")) == \
        "No capture device matching 'Stereo Mix'."
    assert "--" not in gui_error_text(RuntimeError("cannot read --glossary: missing"))
    monkeypatch.setattr(i18n, "_override", "zh-TW")
    assert gui_error_text(RuntimeError("模型載入失敗")) == "模型載入失敗"
