"""#5: Ctrl+C exits cleanly (no traceback) - real SIGINT sent to real child processes (POSIX).

The terminal sends SIGINT to the whole process group, so each process involved is checked on its own:
the listen CLI (pipeline threads busy in ASR/MT), the TTS worker child (blocked reading stdin) and the
speak prototype's second Ctrl+C during a slow shutdown. Windows console Ctrl+C (CTRL_C_EVENT) cannot be
sent to a single child from pytest; the Windows code path is the same Python handlers.
"""
import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")

ROOT = Path(__file__).resolve().parents[1]


def spawn(code: str, **kw) -> subprocess.Popen:
    env = {**os.environ, "PYTHONPATH": str(ROOT) + os.pathsep + os.environ.get("PYTHONPATH", ""),
           "PYTHONUNBUFFERED": "1"}
    return subprocess.Popen([sys.executable, "-c", textwrap.dedent(code)], cwd=ROOT, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kw)


def wait_for(stream, marker: bytes, timeout: float = 20.0) -> None:
    end = time.monotonic() + timeout
    buf = b""
    while marker not in buf:
        assert time.monotonic() < end, f"child never printed {marker!r}: {buf!r}"
        line = stream.readline()
        assert line, f"child exited before {marker!r}: {buf!r}"
        buf += line


def finish(p: subprocess.Popen, timeout: float = 10.0) -> tuple[int, str]:
    try:
        _out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        p.kill()
        _out, err = p.communicate()
        pytest.fail(f"child did not exit within {timeout} s after Ctrl+C; stderr: {err.decode(errors='replace')}")
    return p.returncode, err.decode("utf-8", "replace")


def test_listen_cli_ctrl_c_no_traceback():
    """`tlwu` listen mode: Ctrl+C while ASR and the MT worker thread are busy -> exit 0, no traceback."""
    p = spawn("""
        import sys, time
        import numpy as np
        from tinglongwu import cli, pipeline
        from tinglongwu.asr import Recognizer, Transcript
        from tinglongwu.segment import Segmenter
        from tinglongwu.sink import TextSink
        from tinglongwu.translate import Translation, Translator

        class Live:
            def frames(self):
                while True:
                    time.sleep(0.05)
                    yield np.full(800, 0.1, np.float32)
            def close(self):
                pass

        class ASR(Recognizer):
            def transcribe(self, audio):
                time.sleep(0.1)
                return Transcript("privet", "ru")

        class SlowMT(Translator):
            def translate(self, text, src, tgt):
                print("busy", file=sys.stderr, flush=True)
                time.sleep(0.3)
                return Translation(None, "ni hao")

        cli.run_session = lambda a, **k: pipeline.run(Live(), Segmenter(0.2, use_vad=False), ASR(), SlowMT(),
                                                      TextSink(), "zh")
        sys.exit(cli.main([]))
    """)
    wait_for(p.stderr, b"busy")
    p.send_signal(signal.SIGINT)
    rc, err = finish(p)
    assert "Traceback" not in err and "KeyboardInterrupt" not in err, err
    assert rc == 0


def test_tts_worker_survives_terminal_ctrl_c():
    """The TTS worker child gets the terminal's Ctrl+C too: it must keep running (the parent ends it) and
    then quit cleanly, not die with a traceback in `for line in sys.stdin`."""
    p = spawn("""
        import sys
        from tinglongwu.reverse import tts_worker as w

        def fake_vosk(a):  # same protocol loop as run_vosk, without the TTS model
            w.send({"t": "ready"})
            for line in sys.stdin:
                if "quit" in line:
                    break
            return 0

        w.run_vosk = fake_vosk
        sys.argv = ["tts_worker", "--model", "unused"]
        raise SystemExit(w.main())
    """, stdin=subprocess.PIPE)
    wait_for(p.stdout, b'"ready"')
    p.send_signal(signal.SIGINT)
    time.sleep(0.5)
    assert p.poll() is None, "worker died on Ctrl+C: " + p.stderr.read().decode(errors="replace")
    p.stdin.write(b'{"cmd": "quit"}\n')
    p.stdin.flush()
    rc, err = finish(p)
    assert "Traceback" not in err and "KeyboardInterrupt" not in err, err
    assert rc == 0


def test_speak_proto_second_ctrl_c_during_shutdown():
    """Speak prototype: first Ctrl+C starts shutdown, a second one while shutdown is slow -> 130, no traceback."""
    p = spawn("""
        import sys, time
        from tinglongwu.reverse import proto

        def _main(argv, vm):
            print("listening", file=sys.stderr, flush=True)
            try:
                while True:
                    time.sleep(0.05)
            except KeyboardInterrupt:
                print("stopping", file=sys.stderr, flush=True)
            finally:
                print("cleanup", file=sys.stderr, flush=True)
                time.sleep(30)  # slow cleanup (models, audio devices)
            return 0

        proto._main = _main
        sys.exit(proto.main([]))
    """)
    wait_for(p.stderr, b"listening")
    p.send_signal(signal.SIGINT)
    # wait until the child is inside the slow cleanup: a Ctrl+C that lands between printing "stopping" and
    # entering `finally` is raised in the except block, and the 30 s cleanup then runs uninterrupted (CI race)
    wait_for(p.stderr, b"cleanup")
    p.send_signal(signal.SIGINT)
    rc, err = finish(p)
    assert "Traceback" not in err, err
    assert rc == 130
