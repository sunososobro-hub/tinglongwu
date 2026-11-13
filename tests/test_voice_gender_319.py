"""#319: --voice male|female picks a Vosk Russian id by measured pitch; --speaker wins; bad combos are one-line errors."""
import numpy as np
import pytest

from tinglongwu.reverse import proto
from tinglongwu.reverse import voice_pick as vp

from test_reverse_en import _parse

SR = 24000


def voiced(f0, seconds=1.0, sr=SR):
    """Harmonic 'vowel' at f0 with a pause in the middle (pauses must not drag the median)."""
    t = np.arange(int(sr * seconds)) / sr
    x = sum(np.sin(2 * np.pi * f0 * k * t) / k for k in range(1, 6)).astype(np.float32)
    x[len(x) // 3: len(x) // 2] = 0.0
    return x * 0.3


@pytest.mark.parametrize("f0", [100, 130, 190, 230])
def test_median_f0_measures_pitch(f0):
    got = vp.median_f0(voiced(f0), SR)
    assert got == pytest.approx(f0, rel=0.05)


def test_median_f0_silence_is_none():
    assert vp.median_f0(np.zeros(SR, np.float32), SR) is None
    assert vp.gender(None) is None


def fake_probe(f0s):
    calls = []

    def probe(sid):
        calls.append(sid)
        return voiced(f0s[sid]), SR
    return probe, calls


def test_pick_lowest_id_of_each_gender():
    f0s = {0: 210, 1: 120, 2: 230, 3: 105, 4: 115}
    probe, calls = fake_probe(f0s)
    sid, table = vp.pick_voice("female", probe)
    assert sid == 0 and calls == [0, 1, 2, 3, 4]
    assert table[1] == pytest.approx(120, rel=0.05)
    assert vp.pick_voice("male", fake_probe(f0s)[0])[0] == 1


def test_no_matching_gender_is_an_error():
    with pytest.raises(ValueError, match="no female voice"):
        vp.pick_voice("female", fake_probe({i: 110 for i in range(5)})[0])
    with pytest.raises(ValueError, match="male or female"):
        vp.pick_voice("child", fake_probe({})[0])


class FakeTTS:
    """TTSClient stand-in: speak() yields chunk/done events with the current speaker's pitch."""

    def __init__(self, f0s):
        self.f0s, self.speaker, self.spoken = f0s, 4, []

    def speak(self, text, lang=None):
        self.spoken.append((self.speaker, text))
        yield {"t": "chunk", "id": 1, "sr": SR}, voiced(self.f0s[self.speaker]).tobytes()
        yield {"t": "done", "id": 1}, None


def test_client_probe_drives_the_tts_and_restores_speaker():
    tts = FakeTTS({0: 220, 1: 125, 2: 200, 3: 240, 4: 110})
    sid, _ = vp.pick_voice("male", vp.client_probe(tts))
    assert sid == 1
    assert tts.speaker == 4  # probe leaves the speaker as it was; the caller sets the picked one
    assert [s for s, _ in tts.spoken] == [0, 1, 2, 3, 4] and tts.spoken[0][1] == vp.PROBE_TEXT


def test_cli_voice_with_vosk(tmp_path):
    a = _parse(["--voice", "female", "--tts-engine", "vosk", "--tts-model", str(tmp_path)])
    assert (a.voice, a.speaker) == ("female", 4)


def test_cli_speaker_wins_over_voice(tmp_path, capsys):
    a = _parse(["--voice", "male", "--speaker", "2", "--tts-engine", "vosk", "--tts-model", str(tmp_path)])
    assert (a.voice, a.speaker) == (None, 2)
    assert "--voice male ignored" in capsys.readouterr().err


def test_cli_invalid_voice_is_one_line_error(capsys):
    with pytest.raises(SystemExit) as e:
        proto.build_parser().parse_args(["--voice", "child"])
    assert e.value.code == 2
    err = [ln for ln in capsys.readouterr().err.splitlines() if "error" in ln]
    assert len(err) == 1 and "--voice" in err[0]


def test_cli_voice_needs_vosk(capsys):
    with pytest.raises(SystemExit):
        _parse(["--voice", "female", "--to", "en", "--tts-engine", "kokoro", "--tts-model", "x"])
    assert "--voice picks a Vosk Russian voice" in capsys.readouterr().err


def test_help_lists_the_ids():
    h = proto.build_parser().format_help()
    assert "--voice" in h and "0-4" in h and "165 Hz" in h


def test_cli_voice_rejected_in_follow_mode(capsys):
    with pytest.raises(SystemExit):
        _parse(["--voice", "female", "--to", "follow", "--no-tts"])
    assert "with --to follow use --speaker N" in capsys.readouterr().err


def _auto_ns(**kw):
    import argparse

    base = dict(to="ru", tts_engine="auto", tts_model="", models_dir=None, voice="female", speaker=None)
    return argparse.Namespace(**{**base, **kw})


def test_auto_engine_prefers_vosk_for_voice_unless_speaker_given(tmp_path, monkeypatch):
    from tinglongwu import models

    piper, vosk = tmp_path / "piper", tmp_path / "vosk"
    monkeypatch.setattr(models, "default_piper_ru", lambda mdir=None: piper)
    monkeypatch.setattr(models, "default_vosk_ru", lambda mdir=None: vosk)
    a = _auto_ns()
    proto.resolve_tts_engine(a)
    assert a.tts_engine == "vosk"
    a = _auto_ns(speaker=2)  # --speaker wins, so --voice is dropped: keep the default Piper voice
    proto.resolve_tts_engine(a)
    assert (a.tts_engine, a.tts_model) == ("piper", str(piper))
