"""#474: speak mode picks a downloaded Whisper size when --whisper is not given (like listen mode)."""
from tinglongwu.reverse.proto import default_whisper


def _mk(tmp_path, *sizes):
    for s in sizes:
        (tmp_path / f"whisper-{s}").mkdir()
    return tmp_path


def test_cuda_prefers_turbo_when_downloaded(tmp_path):
    assert default_whisper(None, "cuda", _mk(tmp_path, "small", "large-v3-turbo")) == "large-v3-turbo"


def test_cuda_falls_back_to_downloaded_small(tmp_path, capsys):
    assert default_whisper(None, "cuda", _mk(tmp_path, "small")) == "small"
    assert "large-v3-turbo is not downloaded" in capsys.readouterr().out


def test_cpu_default_small(tmp_path):
    assert default_whisper(None, "cpu", _mk(tmp_path, "small", "large-v3-turbo")) == "small"


def test_explicit_whisper_is_kept(tmp_path):
    assert default_whisper("medium", "cuda", _mk(tmp_path, "small")) == "medium"


def test_nothing_downloaded_keeps_default_for_the_setup_error(tmp_path):
    assert default_whisper(None, "cuda", tmp_path) == "large-v3-turbo"
