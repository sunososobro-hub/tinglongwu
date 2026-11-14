"""#896: a missing output device (no VB-CABLE) never blocks listening."""
import sys
import types

from tinglongwu.reverse.player import Player


def _fake_soundcard(monkeypatch, names):
    sc = types.SimpleNamespace(
        all_speakers=lambda: [types.SimpleNamespace(name=n) for n in names],
        default_speaker=lambda: types.SimpleNamespace(name="default"),
    )
    monkeypatch.setitem(sys.modules, "soundcard", sc)
    monkeypatch.setattr("tinglongwu.capture._soundcard.patch_mixformat", lambda: None)


def test_player_no_device_disabled_skips_lookup(monkeypatch):
    monkeypatch.setitem(sys.modules, "soundcard", None)  # importing it would fail
    p = Player("CABLE Input", enabled=False)
    assert p.enabled is False and p.notice is None


def test_player_no_device_enabled_falls_back_with_notice(monkeypatch, capsys):
    _fake_soundcard(monkeypatch, ["Realtek Speakers", "Realtek Digital Output"])
    monkeypatch.setenv("TLWU_UI_LANG", "en")
    p = Player("CABLE Input", enabled=True)  # must not raise LookupError
    assert p.enabled is False
    assert "CABLE Input" in p.notice and "VB-CABLE" in p.notice
    assert "LookupError" not in capsys.readouterr().err
    p.push(None, 24000)  # no-op, no crash
    p.close()
