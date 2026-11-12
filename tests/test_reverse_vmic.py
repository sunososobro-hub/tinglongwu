import subprocess
from types import SimpleNamespace

import pytest

from tinglongwu.reverse import vmic
from tinglongwu.reverse.live import check_mic_feedback
from tinglongwu.reverse.proto import build_translator


def test_load_commands_exact():
    c = vmic.load_commands()
    assert c[0] == ["pactl", "load-module", "module-null-sink", "sink_name=tlwu_sink",
                    "sink_properties=device.description=tlwu_sink"]
    assert c[1] == ["pactl", "load-module", "module-remap-source", "master=tlwu_sink.monitor",
                    "source_name=tlwu_mic", "source_properties=device.description=tlwu_mic"]
    assert vmic.unload_command("7") == ["pactl", "unload-module", "7"]


def _fake(calls, fail_on=None):
    def run(cmd):
        calls.append(cmd)
        if fail_on and fail_on in cmd[2]:
            return SimpleNamespace(returncode=1, stdout="", stderr="boom")
        return SimpleNamespace(returncode=0, stdout=f"{40 + len(calls)}\n", stderr="")
    return run


def test_open_close_unloads_in_reverse_order():
    calls = []
    v = vmic.VirtualMic(run=_fake(calls)).open()
    assert v.ids == ["41", "42"]
    v.close()
    v.close()  # idempotent
    assert calls[2:] == [["pactl", "unload-module", "42"], ["pactl", "unload-module", "41"]]


def test_failure_rolls_back_and_raises():
    calls = []
    with pytest.raises(RuntimeError, match="boom"):
        vmic.VirtualMic(run=_fake(calls, fail_on="module-remap-source")).open()
    assert calls[-1] == ["pactl", "unload-module", "41"]


def test_missing_pactl():
    def run(cmd):
        raise FileNotFoundError
    with pytest.raises(RuntimeError, match="pactl not found"):
        vmic.VirtualMic(run=run).open()


def test_context_manager_and_platform(monkeypatch):
    calls = []
    with vmic.VirtualMic(run=_fake(calls)):
        pass
    assert len(calls) == 4
    monkeypatch.setattr(vmic.sys, "platform", "win32")
    assert not vmic.is_linux()
    monkeypatch.setattr(vmic.sys, "platform", "linux")
    assert vmic.is_linux()


def test_feedback_guard_refuses_virtual_mic():
    with pytest.raises(ValueError, match="feedback guard"):
        check_mic_feedback("tlwu_mic", "tlwu_sink")
    with pytest.raises(ValueError):
        check_mic_feedback("Monitor of tlwu_sink", None)


def _gguf(tmp_path):
    from tinglongwu.hymt import hymt_path

    f = hymt_path(tmp_path)
    f.parent.mkdir(parents=True)
    f.write_bytes(b"gguf")


def test_build_translator_on_cpu_is_hymt_on_the_cpu_never_opus(monkeypatch, tmp_path):
    """#331: CPU + Hy-MT2 installed -> Hy-MT2 direct on the CPU for zh->ru and the back-translation, no OPUS."""
    import tinglongwu.translate as tr

    monkeypatch.setattr(tr.PivotTranslator, "preload", lambda *a: pytest.fail("OPUS must not load"))
    _gguf(tmp_path)
    made = []

    class Hy:
        def __init__(self, mdir, variant, device):
            made.append(device)

        def preload(self, *a):
            pass

        def translate(self, text, src, tgt):
            return SimpleNamespace(text=f"{src}>{tgt}:{text}")

    warned = []
    mt = build_translator("auto", "cpu", tmp_path, hy_factory=Hy, warn=warned.append)
    assert made == ["cpu"] and mt.translate("你好", "zh", "ru").text == "zh>ru:你好"
    assert mt.translate("привет", "ru", "zh").text == "ru>zh:привет" and mt.history == []
    assert any("1.5 s" in w for w in warned)  # the CPU expectation is stated at startup


def test_build_translator_on_cpu_without_hymt_says_how_to_install(tmp_path):
    from tinglongwu.models import ModelNotFound

    with pytest.raises(ModelNotFound, match="tlwu-setup --hymt"):
        build_translator("auto", "cpu", tmp_path)


def test_build_translator_hymt_that_cannot_load_exits_with_one_line(tmp_path):
    """GGUF there but llama-cpp-python broken: exit with the reason and the setup command, no OPUS fallback."""
    _gguf(tmp_path)

    def broken(*a):
        raise ImportError("No module named 'llama_cpp'")

    with pytest.raises(SystemExit) as e:
        build_translator("auto", "cpu", tmp_path, hy_factory=broken, warn=lambda m: None)
    msg = str(e.value)
    assert "llama_cpp" in msg and "tlwu-setup --hymt" in msg and "no fallback engine" in msg


def test_hymt_failing_mid_session_raises_instead_of_switching_to_opus(tmp_path):
    _gguf(tmp_path)

    class Hy:
        def __init__(self, *a):
            pass

        def preload(self, *a):
            pass

        def translate(self, *a):
            raise RuntimeError("llama crashed")

    warned = []
    mt = build_translator("auto", "cpu", tmp_path, hy_factory=Hy, warn=warned.append)
    with pytest.raises(RuntimeError, match="llama crashed.*no fallback engine"):
        mt.translate("你好", "zh", "ru")
    assert not any("pivot" in w for w in warned)


# ---- macOS: BlackHole 16ch = virtual mic (speech out), BlackHole 2ch = system-audio capture ----
def test_pick_mac_vmic():
    names = ["MacBook Pro Speakers", "BlackHole 2ch", "BlackHole 16ch", "Multi-Output Device"]
    assert vmic.pick_mac_vmic(names) == "BlackHole 16ch"
    assert vmic.pick_mac_vmic(["x", "blackhole 16CH"]) == "blackhole 16CH"
    with pytest.raises(RuntimeError, match="blackhole-16ch"):
        vmic.pick_mac_vmic(["MacBook Pro Speakers", "BlackHole 2ch"])


def test_loop_prone_output():
    for bad in ("BlackHole 2ch", "Multi-Output Device", "多重輸出裝置", "tlwu-out", "Aggregate Device"):
        assert vmic.loop_prone_output(bad), bad
    for ok in ("BlackHole 16ch", "MacBook Pro Speakers", "AirPods Pro", None, ""):
        assert not vmic.loop_prone_output(ok), ok


def test_is_macos(monkeypatch):
    monkeypatch.setattr(vmic.sys, "platform", "darwin")
    assert vmic.is_macos() and not vmic.is_linux()


def test_feedback_guard_refuses_blackhole_mic():
    for n in ("BlackHole 16ch", "BlackHole 2ch"):
        with pytest.raises(ValueError, match="feedback guard"):
            check_mic_feedback(n, "BlackHole 16ch", same_name_ok=True)
    check_mic_feedback("MacBook Pro Microphone", "BlackHole 16ch")  # real mic is fine


def test_player_mac_stereo_and_loop_guard(monkeypatch):
    import sys
    import types

    import numpy as np

    from tinglongwu.reverse import proto

    played = []

    class Player:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def play(self, data):
            played.append(data.shape)

    def make_sc(names):
        speakers = [types.SimpleNamespace(name=n, player=lambda **kw: Player()) for n in names]
        return types.SimpleNamespace(all_speakers=lambda: speakers, default_speaker=lambda: speakers[0])

    monkeypatch.setattr(proto.sys, "platform", "darwin")
    monkeypatch.setitem(sys.modules, "soundcard", make_sc(["Multi-Output Device", "BlackHole 16ch"]))
    p = proto.Player("BlackHole 16ch", 24000)
    p.push(np.zeros(240, dtype=np.float32), 24000)
    p.close()
    assert p.speaker_name == "BlackHole 16ch" and played and played[0][1] == 2
    with pytest.raises(RuntimeError, match="loop back"):
        proto.Player(None, 24000)  # default output is the Multi-Output Device


def test_build_translator_ja_preload_oserror_is_one_line_not_a_traceback(tmp_path):
    """--to ja re-raises Hy-MT2 preload errors (no ja fallback); an OSError (bad llama.dll) still exits with one line."""
    _gguf(tmp_path)

    class Hy:
        def __init__(self, *a):
            pass

        def preload(self, *a):
            raise OSError("llama.dll: the specified module could not be found")

    with pytest.raises(SystemExit) as e:
        build_translator("auto", "cpu", tmp_path, "ja", hy_factory=Hy, warn=lambda m: None)
    assert "llama.dll" in str(e.value) and "--force-reinstall" in str(e.value)
