"""#632: the `fm` subprocess pipe must be UTF-8 whatever the terminal locale (macOS LANG=C / POSIX)."""
import os
import subprocess
import sys
import textwrap

ZH = "你好，世界。今天天氣很好"

CHILD = textwrap.dedent(
    """
    import sys
    from tinglongwu import fm
    # stand-in for the `fm` binary: reads UTF-8 bytes from stdin, answers with the same bytes
    echo = "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())"
    code, out, err = fm._run([sys.executable, "-c", echo], {zh})
    sys.stdout.buffer.write(out.encode("utf-8"))
    """
).format(zh=ascii(ZH))


def _run_under_locale(locale: str) -> bytes:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("LC_", "PYTHON"))}
    env.update(LANG=locale, LC_ALL=locale, PYTHONUTF8="0", PYTHONCOERCECLOCALE="0")
    p = subprocess.run([sys.executable, "-c", CHILD], env=env, capture_output=True, timeout=60)
    assert p.returncode == 0, p.stderr.decode("utf-8", "replace")
    return p.stdout


def test_fm_pipe_roundtrips_chinese_under_c_locale():
    assert _run_under_locale("C").decode("utf-8") == ZH


def test_fm_pipe_roundtrips_chinese_under_posix_locale():
    assert _run_under_locale("POSIX").decode("utf-8") == ZH
