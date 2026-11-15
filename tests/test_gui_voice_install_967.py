"""#967: speak with the Kokoro voice missing offers a one-click install (no network, no tkinter)."""

from tinglongwu.gui import voice_install
from tinglongwu.gui.core import run_voice_install, voice_install_cmd


def test_voice_install_offer_missing_kokoro(tmp_path):
    run = {"voice": True}
    assert voice_install.offer_needed(run, tmp_path, str(tmp_path / "no-python"), str(tmp_path / "no-model"))
    assert "Kokoro" in voice_install.dialog_text("en")


def test_voice_install_offer_not_needed_when_voice_off_or_installed(tmp_path):
    py = tmp_path / "python.exe"
    py.write_text("")
    model = tmp_path / "kokoro"
    model.mkdir()
    (model / "kokoro.onnx").write_text("x")
    assert not voice_install.offer_needed({"voice": False}, tmp_path, "nope", "nope")
    assert not voice_install.offer_needed({"voice": True}, tmp_path, str(py), str(model))
    assert voice_install.offer_needed({"voice": True}, tmp_path, str(py), str(tmp_path / "empty"))


def test_voice_install_cmd_runs_talk_child_and_reports(tmp_path):
    cmd = voice_install_cmd(str(tmp_path), bundled=False)
    assert "--talk" in cmd and "--whisper" not in cmd and "--packs" not in cmd

    class P:
        stdout = [b"Kokoro venv ready\n"]

        def wait(self):
            return 0

    seen = []
    got = []
    rc = run_voice_install(seen.append, str(tmp_path), popen=lambda c, **k: got.append(c) or P(), bundled=False)
    assert rc == 0 and got[0] == cmd
    assert seen[-1] == ("pack_done", "voice", 0) and seen[0][0] == "pack_line"
