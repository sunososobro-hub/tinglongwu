"""#974 speak smoke (tinglongwu/speaksmoke.py): the GUI's speak press as the CI job runs it, and its verdict."""
import numpy as np

from tinglongwu import speaksmoke as s
from tinglongwu.i18n import t


def test_speak_start_zh_en_with_cable_is_mic_zh_to_en_voice_on_cable():
    run, hint, argv = s.speak_start("zh", "en", "CABLE Input (VB-Audio Virtual Cable)")
    assert s.pair_of(argv) == "zh->en"
    assert "--mic" in argv
    assert run["voice"] is True and argv[argv.index("--voice-device") + 1].startswith("CABLE Input")
    assert hint == "gui_ready_speak_cable"


def test_speak_start_ignores_saved_system_source_and_old_target():
    # #973: a saved "PC sound" source and a saved en->zh listen setup must not leak into speak
    run, _, argv = s.speak_start("zh", "en", "CABLE Input", settings={"source": "system", "lang": "zh"})
    assert "--mic" in argv and s.pair_of(argv) == "zh->en"


def test_speak_start_windows_without_cable_turns_voice_off():
    from tinglongwu.gui.core import DEFAULTS, quick_settings, speak_route

    run = quick_settings({**DEFAULTS, "target": "zh", "lang": "en"}, "speak")
    assert speak_route(run, None, platform="win32") == "gui_speak_no_cable" and run["voice"] is False
    r = {"pair": "zh->en", "mic": True, "voice": False, "hint": "gui_speak_no_cable", "speech_s": 0.0,
         "rms_dbfs": -120.0, "voice_off": []}
    assert any(w.startswith("voice not on") for w in s.verdict(r, "zh", "en"))


def test_voice_keys_finds_the_red_line():
    notices = ["something else", t("voice_not_installed")]
    assert s.voice_keys(notices) == ["voice_not_installed"]


def _report(**kw):
    return {"pair": "zh->en", "mic": True, "voice": True, "hint": "gui_ready_speak_cable", "speech_s": 3.2,
            "rms_dbfs": -18.0, "voice_off": [], **kw}


def test_verdict_pass_and_each_failure():
    assert s.verdict(_report(), "zh", "en") == []
    assert s.verdict(_report(voice_off=["voice_not_installed"]), "zh", "en") == ["voice off: voice_not_installed"]
    assert s.verdict(_report(pair="zh->zh"), "zh", "en") == ["speak pair zh->zh != zh->en"]
    assert s.verdict(_report(mic=False), "zh", "en") == ["speak mode does not capture the microphone"]
    assert len(s.verdict(_report(speech_s=0.0, rms_dbfs=-120.0), "zh", "en")) == 1
    assert len(s.verdict(_report(rms_dbfs=-40.0), "zh", "en")) == 1
    assert s.verdict(_report(error="boom"), "zh", "en") == ["boom"]


def test_speech_rms_tone_vs_silence():
    sr = 48000
    tone = 0.5 * np.sin(2 * np.pi * 440 * np.arange(sr) / sr).astype(np.float32)
    sp, rms = s.speech_rms(np.concatenate([np.zeros(sr, np.float32), tone]), sr)
    assert sp == 1.0 and -10 < rms < -5  # 0.5 amplitude sine = -9 dBFS, the silent second is not counted
    assert s.speech_rms(np.zeros(sr, np.float32), sr) == (0.0, -120.0)


def test_main_fails_when_the_voice_is_not_installed(monkeypatch, tmp_path, capsys):
    # what v0.3.5 does after the wizard: no Kokoro, the session shows the red line and nothing reaches the cable
    monkeypatch.setattr(s, "run", lambda a: _report(voice_missing=True, voice_off=["voice_not_installed"],
                                                    speech_s=0.0, rms_dbfs=-120.0))
    out = tmp_path / "r.json"
    assert s.main(["--json", str(out)]) == 1
    assert "voice off: voice_not_installed" in capsys.readouterr().out
    assert '"verdict": "FAIL"' in out.read_text("utf-8")


def test_tlwu_exe_dispatches_speak_smoke(monkeypatch):
    import tlwu_app.main as m

    seen = []
    monkeypatch.setattr(s, "main", lambda argv: seen.append(argv) or 0)
    monkeypatch.setattr(m, "prepare_env", lambda bundled, cpu: None)
    assert m.main(["speak-smoke", "--mine", "zh", "--other", "en"]) == 0
    assert seen == [["--mine", "zh", "--other", "en"]]


def test_window_open_start_deadline_only_until_the_mic_opens():
    assert s.window_open(5.0, 10.0, []) and not s.window_open(11.0, 10.0, [])
    assert s.window_open(20.0, 10.0, [9.0])  # mic opened at 9 s: the full 30 s window, not the 10 s start limit
    assert not s.window_open(9.0 + s.WINDOW_S + 0.1, 10.0, [9.0])


def test_clip_mic_frames_then_close_like_pipeline_run(monkeypatch):
    import threading

    monkeypatch.setattr(s.time, "sleep", lambda _x: None)
    opened, t_clip, stop = [], [], threading.Event()
    mic = s.ClipMic(np.ones(16000, np.float32), lambda: opened.append(1), stop, t_clip)
    it = mic.frames()
    first = next(it)
    assert opened == [1] and len(t_clip) == 1 and len(first) == 8000 and first[0] == 1.0
    mic.close()  # pipeline.run() calls source.close() at the end (#974 review: AttributeError before)
    assert stop.is_set() and list(it) == []
