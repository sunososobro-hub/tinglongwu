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
        for h in logging.getLogger("tinglongwu").handlers:
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
