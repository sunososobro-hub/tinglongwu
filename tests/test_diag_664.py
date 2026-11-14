import logging

from tinglongwu import diag


def test_swallowed_error_lands_in_log_file(tmp_path, monkeypatch):
    monkeypatch.setenv("TLWU_LOG_DIR", str(tmp_path))
    diag.reset()
    try:
        try:
            raise ValueError("boom-664")
        except Exception:
            diag.swallowed("test")
        for h in logging.getLogger(diag.LOGGER).handlers:
            h.flush()
        text = (tmp_path / diag.LOG_NAME).read_text("utf-8")
        assert "boom-664" in text and "ValueError" in text
    finally:
        diag.reset()


def test_unwritable_log_dir_does_not_raise(tmp_path, monkeypatch):
    f = tmp_path / "file"
    f.write_text("x")
    monkeypatch.setenv("TLWU_LOG_DIR", str(f / "sub"))
    diag.reset()
    try:
        try:
            raise RuntimeError("x")
        except Exception:
            diag.swallowed("test")
    finally:
        diag.reset()


def test_init_retries_after_failure(tmp_path, monkeypatch):
    f = tmp_path / "file"
    f.write_text("x")
    monkeypatch.setenv("TLWU_LOG_DIR", str(f / "sub"))
    diag.reset()
    try:
        try:
            raise RuntimeError("first")
        except Exception:
            diag.swallowed("test")
        good = tmp_path / "good"
        monkeypatch.setenv("TLWU_LOG_DIR", str(good))
        try:
            raise RuntimeError("second-664")
        except Exception:
            diag.swallowed("test")
        for h in logging.getLogger(diag.LOGGER).handlers:
            h.flush()
        assert "second-664" in (good / diag.LOG_NAME).read_text("utf-8")
    finally:
        diag.reset()


def test_write_failure_is_silent(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TLWU_LOG_DIR", str(tmp_path))
    diag.reset()
    try:
        try:
            raise RuntimeError("x")
        except Exception:
            diag.swallowed("test")
        h = logging.getLogger(diag.LOGGER).handlers[0]
        h.stream.close()
        h.stream = None
        monkeypatch.setattr(h, "emit", lambda r: h.handleError(r))
        try:
            raise RuntimeError("y")
        except Exception:
            diag.swallowed("test")
        assert capsys.readouterr().err == ""
    finally:
        diag.reset()


def test_package_warnings_still_reach_stderr_after_swallowed(tmp_path, monkeypatch, capsys):
    """#669 review: after diag has set up its file log, a tinglongwu.llamacheck warning (#398) still prints."""
    from tinglongwu import llamacheck

    monkeypatch.setenv("TLWU_LOG_DIR", str(tmp_path))
    # As in the app: no root handlers (pytest adds its own), so an unhandled warning goes to logging.lastResort.
    monkeypatch.setattr(logging.getLogger(), "handlers", [])
    diag.reset()
    try:
        try:
            raise RuntimeError("z")
        except Exception:
            diag.swallowed("test")
        assert logging.getLogger("tinglongwu").handlers == []
        llamacheck._log.warning("llama_cpp: CUDA build not used (probe-664)")
        assert "CUDA build not used (probe-664)" in capsys.readouterr().err
        assert "probe-664" not in (tmp_path / diag.LOG_NAME).read_text("utf-8")
    finally:
        diag.reset()


def test_pipeline_swallowed_error_lands_in_log(tmp_path, monkeypatch):
    """Acceptance: an error the speak pipeline swallows on purpose (TTS warm-up) is written to the file log."""
    from tinglongwu.reverse import synth

    class BadTTS:
        def speak(self, text):
            raise OSError("tts-warm-664")

    monkeypatch.setenv("TLWU_LOG_DIR", str(tmp_path))
    diag.reset()
    try:
        synth.warm_tts(BadTTS(), "ru")  # must not raise
        for h in logging.getLogger(diag.LOGGER).handlers:
            h.flush()
        text = (tmp_path / diag.LOG_NAME).read_text("utf-8")
        assert "tts-warm-664" in text and "tinglongwu.reverse.synth" in text
    finally:
        diag.reset()
