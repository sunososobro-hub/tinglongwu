"""#313 part 1: `tlwu doctor` reports each setup item with a fix action (devices, GPU and packages mocked)."""
from pathlib import Path

import pytest

from tinglongwu import cli, doctor
from tinglongwu.doctor import FAIL, OK, WARN, run_checks

CABLE = ["Speakers (Realtek)", "CABLE Input (VB-Audio Virtual Cable)"]


def _full_models(d: Path, voice: str = "piper") -> Path:
    (d / "whisper-small").mkdir(parents=True)
    hy = d / "hymt2-1.8b"
    hy.mkdir()
    from tinglongwu.hymt import hymt_path

    hymt_path(d).write_bytes(b"x")
    if voice == "piper":
        v = d / "piper-ru_RU-dmitri-medium"
        v.mkdir()
        (v / "ru.onnx").write_bytes(b"x")
        (v / "ru.onnx.json").write_text("{}")
    elif voice == "vosk":
        (d / "vosk-model-tts-ru-0.7-multi").mkdir()
    elif voice == "kokoro":
        (d / "kokoro").mkdir()
        for f in doctor.KOKORO_FILES:
            (d / "kokoro" / f).write_bytes(b"x")
    return d


def _run(d, lang="ru", platform="win32", speakers=(CABLE, "Speakers (Realtek)"), gpu=("RTX 3050", 8192),
         modules=(), kokoro_py=None):
    return {c.name.split(" (")[0]: c for c in run_checks(
        lang, d, platform=platform, speakers=lambda: speakers, gpu=lambda: gpu,
        has_module=lambda m: m in modules, kokoro_python=kokoro_py or d / "no-python")}


@pytest.fixture(autouse=True)
def _no_env(monkeypatch):
    monkeypatch.delenv("VOSK_MODEL", raising=False)


def test_ready_machine_is_all_ok(tmp_path):
    r = _run(_full_models(tmp_path))
    assert {k: c.status for k, c in r.items()} == {k: OK for k in
                                                   ("speech model", "translation model", "voice", "virtual mic",
                                                    "default output", "GPU")}
    assert "CABLE Output" in r["virtual mic"].detail


def test_empty_models_dir_fails_with_setup_commands(tmp_path):
    r = _run(tmp_path)
    assert r["speech model"].status == FAIL and r["speech model"].fix == "tlwu-setup --whisper small"
    assert r["translation model"].status == FAIL and r["translation model"].fix == "tlwu-setup --hymt"
    assert r["voice"].status == FAIL and "--piper-ru" in r["voice"].fix


def test_vosk_model_without_package_is_the_owner_case(tmp_path):
    _full_models(tmp_path, voice="vosk")
    assert _run(tmp_path)["voice"].status == FAIL
    assert "vosk-tts package" in _run(tmp_path)["voice"].detail
    assert _run(tmp_path, modules=("vosk_tts",))["voice"].status == OK


def test_kokoro_voice_needs_files_and_package(tmp_path):
    _full_models(tmp_path, voice="kokoro")
    assert _run(tmp_path, lang="en")["voice"].status == FAIL
    assert _run(tmp_path, lang="en", modules=("kokoro_onnx",))["voice"].status == FAIL  # never in this process
    py = tmp_path / "python.exe"
    py.write_bytes(b"")
    assert _run(tmp_path, lang="en", kokoro_py=py)["voice"].status == OK
    (tmp_path / "kokoro" / "voices-v1.0.bin").unlink()
    c = _run(tmp_path, lang="ja", kokoro_py=py)["voice"]
    assert c.status == FAIL and "voices-v1.0.bin" in c.detail and c.fix == "tlwu-setup --talk"


def test_no_vb_cable_on_windows_fails_with_install_link(tmp_path):
    c = _run(_full_models(tmp_path), speakers=(["Speakers (Realtek)"], "Speakers (Realtek)"))["virtual mic"]
    assert c.status == FAIL and "vb-audio.com" in c.fix


def test_cable_as_default_output_warns_you_will_not_hear(tmp_path):
    c = _run(_full_models(tmp_path), speakers=(CABLE, "CABLE Input (VB-Audio Virtual Cable)"))["default output"]
    assert c.status == WARN and "not hear" in c.detail


def test_platform_specific_virtual_mic(tmp_path):
    _full_models(tmp_path)
    assert _run(tmp_path, platform="linux", speakers=(None, None))["virtual mic"].status == OK
    assert _run(tmp_path, platform="darwin", speakers=(["MacBook Speakers"], "MacBook Speakers"))[
        "virtual mic"].status == FAIL
    assert _run(tmp_path, platform="darwin", speakers=(["BlackHole 16ch"], "x"))["virtual mic"].status == OK
    assert _run(tmp_path, speakers=(None, None))["virtual mic"].status == WARN


def test_gpu_lines(tmp_path):
    _full_models(tmp_path)
    assert _run(tmp_path, gpu=(None, None))["GPU"].status == WARN
    assert _run(tmp_path, gpu=("GTX 1050", 2048))["GPU"].status == WARN
    assert _run(tmp_path, gpu=("RTX 3060", 12288))["GPU"].status == OK


def test_tlwu_doctor_command_prints_one_line_per_check_and_exit_code(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(doctor, "_speakers", lambda: (CABLE, "Speakers (Realtek)"))
    monkeypatch.setattr("tinglongwu.hwprobe.gpu_info", lambda: ("RTX 3050", 8192))
    assert cli.main(["doctor", "--models-dir", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert sum(line.startswith("[") for line in out.splitlines()) == 6
    assert "fix: tlwu-setup --hymt" in out and "problem(s)" in out
    _full_models(tmp_path)
    assert cli.main(["doctor", "--models-dir", str(tmp_path)]) == 0
    assert capsys.readouterr().out.rstrip().endswith("ready for a meeting")


def test_paths_match_what_setup_installs():
    from tinglongwu_setup import downloader, talk

    assert doctor.KOKORO_VENV == talk.default_venv()
    assert set(doctor.KOKORO_FILES) == set(downloader.KOKORO_FILES)
