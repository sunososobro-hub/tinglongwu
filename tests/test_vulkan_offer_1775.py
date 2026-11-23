"""#1775: GUI one-click Vulkan pack + whisper.cpp model offer (pure logic, fake detect / fake runner)."""
import sys

from tinglongwu.gui import vulkan_offer as vo


def test_offer_only_when_vulkan_present_and_pack_missing_or_stale():
    assert vo.state(False, False, False, False) == vo.UNAVAILABLE
    assert vo.state(True, False, False, False) == vo.MISSING
    assert vo.state(True, True, False, False) == vo.STALE  # version-mismatch pack
    assert vo.state(True, True, True, False) == vo.STALE  # whisper.cpp model missing
    assert vo.state(True, True, True, True) == vo.INSTALLED
    offer = lambda st, **k: vo.should_offer(st, **{"nvidia": False, "dismissed": False, "offline": False, **k})  # noqa: E731
    assert offer(vo.MISSING) and offer(vo.STALE)
    assert not offer(vo.INSTALLED) and not offer(vo.UNAVAILABLE)
    assert not offer(vo.MISSING, nvidia=True) and not offer(vo.MISSING, dismissed=True) and not offer(vo.MISSING, offline=True)


def test_detect_with_fake_probes(tmp_path):
    d = lambda **k: vo.detect(tmp_path, platform="linux", **k)  # noqa: E731
    assert d(present=lambda: False) == vo.UNAVAILABLE
    assert d(present=lambda: True, pack=lambda: None, folder=lambda: False) == vo.MISSING
    assert d(present=lambda: True, pack=lambda: None, folder=lambda: True) == vo.STALE
    assert d(present=lambda: True, pack=lambda: tmp_path, folder=lambda: True, ready=lambda: True) == vo.INSTALLED
    assert vo.detect(tmp_path, platform="darwin", present=lambda: True) == vo.UNAVAILABLE


def test_install_cmds_are_the_setup_children(tmp_path):
    pack, model = vo.install_cmds(tmp_path, "small", bundled=False)
    assert pack[:3] == [sys.executable, "-m", "tinglongwu_setup"] and "--vulkan-pack" in pack and "--whisper" not in pack
    assert model[:3] == pack[:3] and model[model.index("--whisper-cpp") + 1] == "small"
    assert "--models-dir" in pack and "--models-dir" in model


def test_run_install_order_stop_on_failure_and_probe(tmp_path):
    vo.INSTALLING.set()
    seen, out = [], []

    def run(name, put, cmd=None):
        seen.append(cmd)
        put(("pack_line", f"ran {cmd}"))
        put(("pack_done", name, 0))  # per-child done must not reach the GUI
        return 0

    rc = vo.run_install(run, out.append, ["a", "b"], probe_fn=lambda m, s: {"asr": True, "mt": False})
    assert rc == 0 and seen == ["a", "b"] and not vo.INSTALLING.is_set()
    assert [e for e in out if e[0] == "pack_done"] == [("pack_done", vo.ROW_NAME, 0)]
    assert any("ASR vulkan, MT cpu" in e[1] for e in out if e[0] == "pack_line")

    seen.clear(), out.clear()
    run2 = lambda name, put, cmd=None: (seen.append(cmd), 1)[1]  # noqa: E731
    assert vo.run_install(run2, out.append, ["a", "b"]) == 1
    assert seen == ["a"] and out == [("pack_done", vo.ROW_NAME, 1)]
