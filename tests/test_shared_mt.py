"""#285: listen+speak in one process: Hy-MT2 is loaded once and both sides take turns on it; one Ctrl+C stops
both; the Linux menu starts one process. No real models: llama_cpp is a fake module that counts loads."""
import os
import subprocess
import sys
import threading
import time
import types
from pathlib import Path

import pytest

from tinglongwu import cli, engines, llamacheck
from tinglongwu.hymt import HyMtTranslator, hymt_path
from tinglongwu.reverse import proto

ROOT = Path(__file__).resolve().parents[1]


class SlowLlm:
    """Records how many callers are inside create_chat_completion at once."""

    def __init__(self):
        self.inside, self.max_inside, self.calls = 0, 0, 0
        self._count = threading.Lock()

    def create_chat_completion(self, messages, **kw):
        with self._count:
            self.inside += 1
            self.calls += 1
            self.max_inside = max(self.max_inside, self.inside)
        time.sleep(0.005)
        with self._count:
            self.inside -= 1
        return {"choices": [{"message": {"content": "hello"}}]}


def _fake_llama_cpp(made):
    m = types.ModuleType("llama_cpp")
    m.__file__ = "nowhere/llama_cpp/__init__.py"
    m.__version__ = "0.3.36"
    m.llama_print_system_info = lambda: b""

    def Llama(**kw):
        made.append(kw)
        return SlowLlm()

    m.Llama = Llama
    return m


@pytest.fixture
def hy_models(tmp_path, monkeypatch):
    made: list = []
    monkeypatch.setitem(sys.modules, "llama_cpp", _fake_llama_cpp(made))
    monkeypatch.setattr(llamacheck, "check_build", lambda mod: None)
    p = hymt_path(tmp_path)
    p.parent.mkdir(parents=True)
    p.write_bytes(b"gguf")
    return tmp_path, made


def test_shared_mt_listen_and_speak_translators_load_hymt_once(hy_models, capsys):
    mdir, made = hy_models
    listen = cli.make_translator("hymt", mdir, "tw", "cpu")  # what run_session builds
    speak = proto.build_translator("hymt", "cpu", mdir, "en", src="zh")  # what proto._main builds
    assert len(made) == 1
    assert listen.hy.llm is speak.hy.llm


def test_shared_mt_concurrent_starts_still_load_once(hy_models):
    """The listen side loads in its thread while the speak side loads in the main thread."""
    mdir, made = hy_models
    built: list = []
    side = proto.ListenSide(["--lang", "ru", "--engine", "hymt"],
                            run=lambda a, stop: built.append(cli.make_translator(a.engine, mdir, "tw", "cpu")))
    side.start()
    speak = proto.build_translator("hymt", "cpu", mdir, "en", src="zh")
    side.close()
    assert side.error is None and len(built) == 1
    assert len(made) == 1 and built[0].hy.llm is speak.hy.llm


def test_shared_mt_two_callers_never_overlap_inside_the_model(hy_models):
    mdir, made = hy_models
    a = HyMtTranslator(mdir, device="cpu", loader=engines.load_llama)
    b = HyMtTranslator(mdir, device="cpu", loader=engines.load_llama)
    errors: list = []

    def side(tr, src, tgt):
        try:
            for _ in range(20):
                tr.translate("привет", src, tgt)
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    ts = [threading.Thread(target=side, args=(a, "ru", "en")), threading.Thread(target=side, args=(b, "zh", "en"))]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not errors
    llm = a.llm.llm
    assert b.llm.llm is llm and len(made) == 1
    assert llm.calls == 40 and llm.max_inside == 1


def test_shared_mt_different_files_or_devices_are_separate(hy_models):
    mdir, made = hy_models
    a = engines.shared_llama(hymt_path(mdir), False)
    b = engines.shared_llama(hymt_path(mdir), True)
    assert a is not b and len(made) == 2


def test_both_mode_listen_args_go_to_the_tlwu_parser():
    a = proto.build_parser().parse_args(["--mic", "--to", "en", "--no-confirm", "--listen", "--lang", "auto",
                                         "--lang-candidates", "en,ru", "--glossary", "C:/My Glossary"])
    assert a.no_confirm and a.listen == ["--lang", "auto", "--lang-candidates", "en,ru", "--glossary", "C:/My Glossary"]
    side = proto.ListenSide(a.listen, run=lambda a, stop: None)
    assert side.args.lang == "auto" and side.args.lang_candidates == "en,ru" and side.args.glossary == "C:/My Glossary"


def test_both_mode_rejects_listen_options_that_redraw_the_terminal(capsys):
    with pytest.raises(SystemExit) as e:
        proto._main(["--mic", "--listen", "--lang", "ru", "--stream"], [])
    assert e.value.code == 2
    assert "--stream is not available next to the speak side" in capsys.readouterr().err


def test_both_mode_listen_error_is_reported_and_close_stops_the_thread(capsys):
    def missing_model(a, stop):
        raise SystemExit("tlwu: the translation model Hy-MT2 is not installed")

    side = proto.ListenSide(["--lang", "ru"], run=missing_model).start()
    side.thread.join(5)
    assert "listen side stopped" in capsys.readouterr().err and "Hy-MT2 is not installed" in str(side.error)

    seen = threading.Event()

    def runs_until_stopped(a, stop):
        seen.set()
        stop.wait()

    side = proto.ListenSide(["--lang", "ru"], run=runs_until_stopped).start()
    assert seen.wait(5)
    side.close()
    assert not side.thread.is_alive() and side.error is None


def test_listen_thread_gets_the_listen_budget_main_thread_keeps_speak(monkeypatch):
    from tinglongwu import threads

    monkeypatch.setenv("TLWU_BOTH", "1")
    monkeypatch.setenv("TLWU_CPU_THREADS", "8")
    monkeypatch.setattr(threads, "_role", None)
    threads.set_role("speak")
    got = {}

    def run(a, stop):
        threads.set_role("listen")
        got["listen"] = threads.current()

    side = proto.ListenSide(["--lang", "ru"], run=run).start()
    side.thread.join(5)
    assert got["listen"] == threads.plan("listen") != threads.plan("speak")
    assert threads.current() == threads.plan("speak")


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
def test_both_mode_one_ctrl_c_stops_both_sides():
    import signal
    import textwrap

    code = textwrap.dedent("""
        import sys, time
        from tinglongwu.reverse import proto

        def listen_run(a, stop):
            print("listen ready", file=sys.stderr, flush=True)
            stop.wait()
            print("listen stopped", file=sys.stderr, flush=True)

        def _main(argv, vm):  # the real _main: start the listen side, then the speak loop until Ctrl+C
            vm.append(proto.ListenSide(["--lang", "ru"], run=listen_run).start())
            try:
                time.sleep(0.2)
                print("speak ready", file=sys.stderr, flush=True)
                while True:
                    time.sleep(0.05)
            except KeyboardInterrupt:
                print("stopping", file=sys.stderr, flush=True)
            return 0

        proto._main = _main
        sys.exit(proto.main([]))
    """)
    env = {**os.environ, "PYTHONPATH": str(ROOT), "PYTHONUNBUFFERED": "1"}
    p = subprocess.Popen([sys.executable, "-c", code], cwd=ROOT, env=env, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE)
    buf = b""
    while b"speak ready" not in buf:
        line = p.stderr.readline()
        assert line, buf
        buf += line
    p.send_signal(signal.SIGINT)
    _out, err = p.communicate(timeout=10)
    err = buf.decode() + err.decode()
    assert p.returncode == 0, err
    assert "listen stopped" in err and "Traceback" not in err


@pytest.mark.skipif(sys.platform == "win32", reason="bash menu")
def test_both_mode_linux_menu_starts_one_process(tmp_path):
    """Menu option 1 (meeting, listen+speak): one `python -m tinglongwu.reverse.proto ... --listen <tlwu args>`,
    no background `tlwu`."""
    from tests.test_menu_listen_opts import _fake_env

    root, log, env, gl = _fake_env(tmp_path)
    stub = Path(log).parent / "bin"
    proto_log = tmp_path / "proto_args"
    real = sys.executable
    (stub / "python").write_text(
        "#!/usr/bin/env bash\n"
        f'if [ "$1" = "-m" ] && [ "$2" = "tinglongwu.reverse.proto" ]; then shift 2; '
        f'printf "%s\\n" "$@" > "{proto_log}"; exit 0; fi\n'
        f'exec "{real}" "$@"\n', "utf-8")
    (stub / "python").chmod(0o755)
    kokoro = tmp_path / "kokoro"
    kokoro.mkdir()
    for f in ("kokoro-v1.0.int8.onnx", "voices-v1.0.bin"):
        (kokoro / f).write_bytes(b"x")
    kpy = tmp_path / "kpy"
    kpy.write_text("#!/bin/sh\nexit 0\n", "utf-8")
    kpy.chmod(0o755)
    mdir = Path(env["TLWU_MODELS"])
    for pair in ("zh-en", "en-zh"):
        (mdir / "opus" / pair).mkdir(parents=True, exist_ok=True)
    env.update(TLWU_LANG="en", KOKORO_PY=str(kpy), KOKORO_MODEL=str(kokoro))
    env.pop("TLWU_BOTH", None)
    # 1 = meeting (listen+speak); Enter = do not save subtitles; Enter = confirm before sending
    r = subprocess.run(["bash", str(root / "scripts" / "linux_menu.sh")], input="1\n\n\n", env=env,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert not Path(log).exists(), "a separate tlwu listener process was started"
    args = proto_log.read_text("utf-8").split("\n")[:-1]
    assert "--mic" in args and args[args.index("--to") + 1] == "en"
    listen = args[args.index("--listen") + 1:]
    assert listen[:2] == ["--lang", "en"] and "--glossary" in listen and str(gl) in listen
    assert "--stream" not in listen
