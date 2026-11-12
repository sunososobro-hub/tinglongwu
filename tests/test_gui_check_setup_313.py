"""#313 part 2: GUI "Check setup" shows the `tlwu doctor` lines (one per check, with its fix). No Tk needed."""
from __future__ import annotations

import queue

from tinglongwu.doctor import FAIL, OK, WARN, Check
from tinglongwu.gui import core

CHECKS = [
    Check("speech model", OK, "Whisper small"),
    Check("translation model", FAIL, "Hy-MT2 missing (x)", "tlwu-setup --hymt"),
    Check("voice (ru)", FAIL, "Vosk Russian model found but the vosk-tts package is not installed",
          "tlwu-setup --vosk-tts-ru"),
    Check("virtual mic", OK, "'CABLE Input' found"),
    Check("default output", WARN, "no default output device found", "plug in a headset"),
    Check("GPU", OK, "RTX 3050, 8192 MiB VRAM"),
]


def test_check_setup_lists_every_check_with_its_fix():
    seen = []
    text, ok = core.setup_check("ru", run=lambda lang: seen.append(lang) or CHECKS)
    assert seen == ["ru"]
    assert ok is False
    lines = text.splitlines()
    for c in CHECKS:  # one line per check, in doctor order
        assert sum(ln.startswith(f"[{c.status:>4}] {c.name}: ") for ln in lines) == 1
    assert [ln for ln in lines if ln.strip().startswith("fix:")] == [
        "         fix: tlwu-setup --hymt", "         fix: tlwu-setup --vosk-tts-ru", "         fix: plug in a headset"]
    assert lines[-1].startswith("2 problem(s)")


def test_check_setup_all_ok_is_ready():
    text, ok = core.setup_check("en", run=lambda lang: [Check("GPU", OK, "x"), Check("speech model", WARN, "y")])
    assert ok is True and text.splitlines()[-1] == "ready for a meeting"


def test_run_setup_check_puts_result_on_gui_queue():
    q: queue.Queue = queue.Queue()
    core.run_setup_check(q, "ru", run=lambda lang: CHECKS)
    kind, text, ok = q.get_nowait()
    assert kind == "doctor" and ok is False and "fix: tlwu-setup --hymt" in text


def test_run_setup_check_reports_a_crashing_probe():
    def boom(lang):
        raise OSError("no audio backend")

    q: queue.Queue = queue.Queue()
    core.run_setup_check(q, "ru", run=boom)
    assert q.get_nowait() == ("doctor", "check failed: OSError: no audio backend", False)


def test_doctor_lang_follows_the_source_language():
    assert [core.doctor_lang(x) for x in ("ru", "en", "ja", "auto", "de", None)] == ["ru", "en", "ja", "ru", "ru", "ru"]


def test_check_setup_runs_real_doctor_probes_on_an_empty_models_dir(tmp_path, monkeypatch):
    """No injected runner: the real run_checks result (with stubbed devices/GPU) reaches the GUI text."""
    from tinglongwu import doctor

    real = doctor.run_checks
    monkeypatch.setattr(doctor, "run_checks", lambda lang: real(
        lang, tmp_path, platform="win32", speakers=lambda: ([], None), gpu=lambda: (None, None),
        has_module=lambda m: False, kokoro_python=tmp_path / "none"))
    text, ok = core.setup_check("ru")
    assert ok is False
    assert "fix: tlwu-setup --hymt" in text and "fix: install VB-CABLE" in text
