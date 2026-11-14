"""#760: countdown / auto send modes with a fake clock; low-confidence lines always wait for "y"."""
import queue

from tinglongwu.reverse import sendmode
from tinglongwu.reverse.confirm import ConfirmSession, Utterance


def _get(inp):
    try:
        return inp.get(timeout=0.3)
    except queue.Empty:
        raise EOFError


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def _run(mode, lines, back="你好嗎", zh="你好嗎", extra=None, no_back=False):
    clk, sent, out = Clock(), [], []
    inp = queue.Queue()
    for ln in lines:
        inp.put(ln)

    def readline():
        try:
            return inp.get(timeout=0.3)
        except queue.Empty:
            raise EOFError

    s = ConfirmSession(lambda ru: ([1, 2], 16000), lambda a, sr: sent.append(a), out=out.append,
                       readline=readline, flush=lambda: None, clock=clk, sleep=clk.sleep, mode=mode,
                       countdown_s=2.0, require_back=not no_back, max_age=0)
    u = Utterance(1, zh, "hello", back, extra=extra or {})
    s.submit(u)
    s.finish()
    s.run()
    return u, sent, out, clk


def test_countdown_sends_without_key_press():
    u, sent, out, clk = _run("countdown", [])
    assert u.decision == "sent" and len(sent) == 1
    assert clk.t >= 2.0  # waited the full countdown


def test_countdown_stop_discards():
    u, sent, _, _ = _run("countdown", ["x"])
    assert u.decision == "discarded" and not sent


def test_low_confidence_mismatch_waits_and_discards_on_eof():
    u, sent, out, _ = _run("countdown", [], back="完全不同的句子", zh="你好嗎")
    assert u.decision == "discarded" and not sent
    assert any("low confidence" in o for o in out)


def test_low_confidence_sent_after_explicit_y():
    u, sent, _, _ = _run("countdown", ["y"], extra={"halluc": True})
    assert u.decision == "sent"  # "y" during the countdown sends at once


def test_auto_sends_confident_and_holds_halluc():
    u, sent, _, _ = _run("auto", [])
    assert u.decision == "sent"
    u2, sent2, _, _ = _run("auto", [], extra={"halluc": True})
    assert u2.decision == "discarded" and not sent2


def test_confirm_mode_unchanged():
    u, sent, _, _ = _run("confirm", [""])
    assert u.decision == "discarded"
    u, sent, _, _ = _run("confirm", ["y"])
    assert u.decision == "sent"


def test_low_confidence_reasons():
    mk = lambda **k: Utterance(1, "你好嗎", "x", k.pop("back", "你好嗎"), extra=k)  # noqa: E731
    assert sendmode.low_confidence(mk()) == ""
    assert "logprob" in sendmode.low_confidence(mk(avg_logprob=-1.5))
    assert sendmode.low_confidence(mk(back="")) != ""
    assert sendmode.low_confidence(mk(back=""), require_back=False) == ""


def test_stale_answer_never_decides_next_utterance():
    clk, sent, out, inp = Clock(), [], [], queue.Queue()
    s = ConfirmSession(lambda ru: ([1], 16000), lambda a, sr: sent.append(a), out=out.append,
                       readline=lambda: _get(inp), flush=lambda: None, clock=clk, sleep=clk.sleep,
                       mode="countdown", countdown_s=2.0, max_age=0)
    u1 = Utterance(1, "你好嗎", "a", "你好嗎")
    s.submit(u1)
    assert s._countdown(u1) is True  # timed out, sent
    inp.put("y")  # typed during playback of u1: belongs to u1
    s._reader[0].join(1)
    u2 = Utterance(2, "你好嗎", "b", "完全不同的句子")  # low confidence
    s.submit(u2)
    assert s._countdown(u2) is None  # the stale "y" did not send it
    s.finish()


def test_voice_command_during_countdown_switches_voice():
    import threading
    import time

    clk, voices, inp, got = Clock(), [], queue.Queue(), threading.Event()
    inp.put("v2")

    def readline():
        if got.is_set():
            threading.Event().wait(10)  # later readers just block (nobody types again)
            raise EOFError
        v = _get(inp)
        got.set()  # the reader consumed the line; only now may the fake clock run
        return v

    def sleep(s):
        got.wait(2)
        time.sleep(0.05)  # let the reader thread put the line on the answer queue
        clk.sleep(s)

    s = ConfirmSession(lambda ru: ([1], 16000), lambda a, sr: None, out=lambda m: None,
                       readline=readline, flush=lambda: None, clock=clk, sleep=sleep,
                       set_voice=voices.append, mode="countdown", countdown_s=2.0, max_age=0)
    u = Utterance(1, "你好嗎", "a", "你好嗎")
    s.submit(u)
    assert s._countdown(u) is True and voices == [2]
    s.finish()


# ---- #822: the CLI default is confirm (owner 2026-11-14), and countdown / auto print the quality notice once

import re  # noqa: E402
from types import SimpleNamespace  # noqa: E402

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from tinglongwu import i18n  # noqa: E402
from tinglongwu.reverse import args as reverse_args  # noqa: E402
from tinglongwu.reverse import proto  # noqa: E402

NOTICE_ZH = "翻譯品質還不夠穩，不建議直接送出；建議先確認翻譯對不對再送出"
NOTICE_EN = ("Translation quality is not stable yet; sending directly is not recommended - "
             "check the translation first, then send.")


class _MT:
    history, _pair = [], None  # the attributes proto / confirm read

    def translate(self, text, src, tgt):
        return SimpleNamespace(text="Hello there." if tgt == "en" else "你好")


class _TTS:
    engine = "none"

    def speak(self, text):
        yield {"t": "chunk", "sr": 24000}, np.ones(4, np.float32).tobytes()
        yield {"t": "done"}, None


class _Player:
    enabled = True

    def push(self, audio, sr):
        pass


def _start_speak(monkeypatch, capsys, send_mode, ui_lang):
    """Start the real speak confirm loop with one line, as `tlwu-reverse --send-mode <mode>` does."""
    monkeypatch.setenv("TLWU_UI_LANG", ui_lang)
    monkeypatch.setattr(i18n, "_override", None)
    monkeypatch.setenv("TLWU_SEND_COUNTDOWN", "0.01")
    monkeypatch.setattr("builtins.input", lambda *a: "y")
    ns = SimpleNamespace(to="en") if send_mode is None else SimpleNamespace(to="en", send_mode=send_mode)
    proto._run_confirm(ns, iter([(1, "mic", "你好")]), _MT(), _TTS(), _Player(), SimpleNamespace(enabled=False),
                       {"items": []})
    return capsys.readouterr().out


def test_cli_send_mode_defaults_to_confirm():
    ap = reverse_args.build_parser()
    assert ap.parse_args([]).send_mode == "confirm"
    assert ap.parse_args(["--send-mode", "countdown"]).send_mode == "countdown"
    assert ap.parse_args(["--send-mode", "auto"]).send_mode == "auto"


def test_help_names_confirm_as_the_default_mode():
    help_ = next(a.help for a in reverse_args.build_parser()._actions if a.dest == "send_mode")
    assert re.search(r"confirm\b[^;]*\(default\)", help_)
    assert not re.search(r"countdown\b[^;]*\(default\)", help_) and not re.search(r"auto\b[^;]*\(default\)", help_)


@pytest.mark.parametrize("mode", ["countdown", "auto"])
@pytest.mark.parametrize("lang,notice", [("zh-TW", NOTICE_ZH), ("en", NOTICE_EN)])
def test_direct_send_modes_print_the_notice_once_in_the_ui_language(monkeypatch, capsys, mode, lang, notice):
    out = _start_speak(monkeypatch, capsys, mode, lang)
    assert out.count(notice) == 1
    assert (NOTICE_EN if lang == "zh-TW" else NOTICE_ZH) not in out  # only the UI language's line


@pytest.mark.parametrize("lang", ["zh-TW", "en"])
def test_confirm_mode_prints_no_notice(monkeypatch, capsys, lang):
    out = _start_speak(monkeypatch, capsys, "confirm", lang)
    assert NOTICE_ZH not in out and NOTICE_EN not in out
    assert "不建議直接送出" not in out and "not recommended" not in out


def test_no_send_mode_given_behaves_as_confirm(monkeypatch, capsys):
    out = _start_speak(monkeypatch, capsys, None, "en")
    assert NOTICE_EN not in out
