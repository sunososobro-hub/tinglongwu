"""#373: meeting mode (speak side + --listen in one process) labels my lines 我 and the other side's 對方,
in the terminal and in the --save transcript."""
import json
import queue
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tinglongwu import fmt
from tinglongwu.reverse import proto
from tinglongwu.sink import Result
from tests.test_overlay_speak_322 import FakeMT, FakePlayer, FakeTTS, _drain, _mock_live


def _listen_run(a, stop, sink=None):
    """Stand-in for cli.run_session: the other side says one Russian line."""
    sink.write_source("ru", "privet")
    sink.write(Result("ru", "privet", None, "你好", tgt="zh"))


def _meeting(tmp_path, mode, bar_q=None):
    """Listen side (thread) + speak side (live loop), wired the way proto._main wires them."""
    save = tmp_path / "t.jsonl"
    side = proto.ListenSide(["--lang", "ru", "--save", str(save)], run=_listen_run, bar_q=bar_q).start()
    side.thread.join(5)
    assert side.error is None
    a = SimpleNamespace(mic_name="Headset Mic", chunk_s=10.0, to="ru", no_back=True, bar_q=bar_q,
                        me_save=side.saver.record)
    pv = FakePlayer()
    pv.enabled = False
    proto._live_main(a, None, FakeMT(), None if mode == "text" else FakeTTS(), FakePlayer(), pv,
                     mode == "confirm", {"items": []})
    return [json.loads(x) for x in save.read_text("utf-8").splitlines()]


@pytest.mark.parametrize("mode", ["auto", "text", "confirm"])
def test_meeting_terminal_and_transcript_carry_both_speakers(tmp_path, monkeypatch, capsys, mode):
    monkeypatch.setenv("TLWU_UI_LANG", "zh-TW")
    _mock_live(monkeypatch)
    if mode == "confirm":
        monkeypatch.setattr("builtins.input", lambda *x: "y")
    rows = _meeting(tmp_path, mode)
    out = capsys.readouterr().out
    assert "◀ 對方 privet" in out and "▶ 我 你好" in out
    by_side = {r["side"]: r for r in rows}
    assert sorted(by_side) == ["me", "them"] and len(rows) == 2
    assert (by_side["them"]["lang"], by_side["them"]["src"], by_side["them"]["dst"]) == ("ru", "privet", "你好")
    assert (by_side["me"]["lang"], by_side["me"]["src"], by_side["me"]["dst"]) == ("zh", "你好", "privet-2")


def test_meeting_with_overlay_still_saves_both_sides(tmp_path, monkeypatch):
    _mock_live(monkeypatch)
    q = queue.Queue()
    rows = _meeting(tmp_path, "auto", bar_q=q)
    assert sorted(r["side"] for r in rows) == ["me", "them"]
    finals = sorted(x for x in _drain(q) if x[0] == "final")
    assert finals == [("final", "privet", "◀ 你好"), ("final", "你好", "▶ privet-2")]


@pytest.mark.parametrize("lang,me,them", [("en", "▶ Me", "◀ Them"), ("ru", "▶ Я", "◀ Собеседник"),
                                          ("zh-TW", "▶ 我", "◀ 對方")])
def test_labels_are_localised_and_coloured_per_side(monkeypatch, lang, me, them):
    monkeypatch.setenv("TLWU_UI_LANG", lang)
    m, o = fmt.head("me", "x", color=True), fmt.head("them", "x", color=True)
    assert f"{fmt.GREEN}{me}{fmt.RESET}" in m and f"{fmt.CYAN}{them}{fmt.RESET}" in o


def test_listen_only_save_marks_lines_them(tmp_path):
    """Plain `tlwu --save` (no speak side): every line is the other side."""
    from tinglongwu.sink import JsonlSaveSink, TextSink
    import io

    s = JsonlSaveSink(TextSink(stream=io.StringIO()), tmp_path / "l.jsonl")
    s.write(Result("en", "hello", None, "你好"))
    assert json.loads((tmp_path / "l.jsonl").read_text("utf-8"))["side"] == "them"


def test_main_wires_my_lines_into_the_listen_side_file(tmp_path, monkeypatch):
    """proto._main: --save after --listen -> the speak side records into the listen side's file."""
    seen = {}
    monkeypatch.setattr(proto, "check_args", lambda a: seen.setdefault("a", a) and None)
    monkeypatch.setattr("tinglongwu.reverse.live.list_mic_names", lambda: [])
    save = tmp_path / "m.jsonl"
    assert proto._main(["--list-mics", "--listen", "--lang", "ru", "--save", str(save)], []) == 0
    seen["a"].me_save(Result("zh", "你好", None, "privet", tgt="ru", side="me"))
    assert json.loads(save.read_text("utf-8"))["side"] == "me"


@pytest.mark.skipif(sys.platform == "win32", reason="bash menu")
def test_linux_menu_meeting_save_goes_to_the_listen_side(tmp_path):
    """Menu meeting mode, answer y to saving: --save is passed after --listen (one file, both sides)."""
    from tests.test_menu_listen_opts import _fake_env

    root, log, env, _gl = _fake_env(tmp_path)
    stub = Path(log).parent / "bin"
    proto_log = tmp_path / "proto_args"
    (stub / "python").write_text(
        "#!/usr/bin/env bash\n"
        f'if [ "$1" = "-m" ] && [ "$2" = "tinglongwu.reverse.proto" ]; then shift 2; '
        f'printf "%s\\n" "$@" > "{proto_log}"; exit 0; fi\n'
        f'exec "{sys.executable}" "$@"\n', "utf-8")
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
    # 1 = meeting; y = save subtitles; Enter = confirm before sending
    r = subprocess.run(["bash", str(root / "scripts" / "linux_menu.sh")], input="1\ny\n\n", env=env,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    args = proto_log.read_text("utf-8").split("\n")[:-1]
    assert "--save" in args[args.index("--listen") + 1:]
