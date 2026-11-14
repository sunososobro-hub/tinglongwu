"""#21: listen-mode latency A/B knobs. --end-silence / TLWU_END_SILENCE (segment end) and --pause-send /
TLWU_PAUSE_SEND (merger release) let the 0.8 -> 0.5 s change be measured on real hardware before it ships;
defaults are unchanged."""
import pytest

from tinglongwu import cli, segment
from tinglongwu.merge import SentenceMerger
from test_cpu_latency import _padded_energy_vad, _wait_after_speech

UNFINISHED = "and then we went over to the station"  # no sentence punctuation, 8 words


def test_merger_pause_send_override_releases_at_shorter_pause():
    default = SentenceMerger(max_wait=6.0)
    assert default.add(UNFINISHED, "en", now=0.0, pause=0.6) == []  # 0.6 s < 0.8 s: held up to max_wait
    fast = SentenceMerger(max_wait=6.0, pause_send=0.5)
    out = fast.add(UNFINISHED, "en", now=0.0, pause=0.6)
    assert [u.text for u in out] == [UNFINISHED]
    # a short pause below the override still holds
    assert SentenceMerger(max_wait=6.0, pause_send=0.5).add(UNFINISHED, "en", now=0.0, pause=0.3) == []


def test_merger_pause_send_override_beats_language_patch():
    # ja ships MERGE_PAUSE_SEND = 0.5; an explicit override wins either way
    m = SentenceMerger(max_wait=6.0, pause_send=1.0)
    assert m.add("今日は駅まで歩いて行きました", "ja", now=0.0, pause=0.7) == []


def test_parse_args_flags_env_and_validation(monkeypatch):
    monkeypatch.delenv("TLWU_END_SILENCE", raising=False)
    monkeypatch.delenv("TLWU_PAUSE_SEND", raising=False)
    a = cli.parse_args(["--lang", "en"])
    assert a.end_silence is None and a.pause_send is None
    a = cli.parse_args(["--lang", "en", "--end-silence", "0.4", "--pause-send", "0.5"])
    assert (a.end_silence, a.pause_send) == (0.4, 0.5)
    monkeypatch.setenv("TLWU_END_SILENCE", "0.45")
    monkeypatch.setenv("TLWU_PAUSE_SEND", "0.5")
    a = cli.parse_args(["--lang", "en"])
    assert (a.end_silence, a.pause_send) == (0.45, 0.5)
    a = cli.parse_args(["--lang", "en", "--pause-send", "0.7"])  # flag beats env
    assert a.pause_send == 0.7
    monkeypatch.setenv("TLWU_PAUSE_SEND", "fast")
    with pytest.raises(SystemExit):
        cli.parse_args(["--lang", "en"])
    monkeypatch.delenv("TLWU_PAUSE_SEND")
    with pytest.raises(SystemExit):
        cli.parse_args(["--lang", "en", "--end-silence", "0"])


def test_listen_end_silence_cuts_wait_after_speech(monkeypatch):
    monkeypatch.delenv("TLWU_END_SILENCE", raising=False)
    monkeypatch.setattr(segment, "silero_vad", _padded_energy_vad)
    base = cli.parse_args(["--lang", "en"])
    fast = cli.parse_args(["--lang", "en", "--end-silence", "0.3"])
    old_wait, _ = _wait_after_speech(lambda: cli.listen_segmenter(base))
    new_wait, new_rep = _wait_after_speech(lambda: cli.listen_segmenter(fast))
    assert new_wait <= old_wait - 0.25, (old_wait, new_wait)
    assert new_rep >= 0.3  # the reported pause still covers the configured silence


def test_end_silence_below_pause_send_warns(monkeypatch, capsys):
    """A cut's reported pause is at least end_silence + vad_pad; below pause-send the early release never fires."""
    monkeypatch.delenv("TLWU_END_SILENCE", raising=False)
    monkeypatch.delenv("TLWU_PAUSE_SEND", raising=False)
    cli.parse_args(["--lang", "en", "--end-silence", "0.3"])  # 0.3 + 0.1 < 0.8 default
    assert "--pause-send" in capsys.readouterr().err
    cli.parse_args(["--lang", "en", "--end-silence", "0.5", "--pause-send", "0.5"])  # 0.6 >= 0.5: fine
    cli.parse_args(["--lang", "en"])
    cli.parse_args(["--lang", "ja", "--end-silence", "0.5"])  # ja's own MERGE_PAUSE_SEND is 0.5: 0.6 >= 0.5
    assert "warning" not in capsys.readouterr().err
    cli.parse_args(["--lang", "auto", "--end-silence", "0.5"])  # unknown language: core 0.8 > 0.6
    assert "may not reach" in capsys.readouterr().err
    # the warned combination really holds the sentence: pause 0.4 < 0.8 is not released
    assert SentenceMerger(max_wait=6.0).add(UNFINISHED, "en", now=0.0, pause=0.4) == []
