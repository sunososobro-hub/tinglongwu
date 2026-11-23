"""#1737: a live session translates one short line before "ready", so the MT backend's first-call setup (2141 ms vs
a 266 ms p50 on Vulkan, #802) is not paid by the first subtitle; the warm-up line never reaches a sink."""
from __future__ import annotations

import io
import sys

from tinglongwu import cli


class Mt:
    def __init__(self, events=None, fail=False):
        self.events = events if events is not None else []
        self.fail = fail
        self.drafts, self.translated = [], []

    def preload(self, src, tgt):
        self.events.append("mt preload")

    def draft(self, text, src, tgt):
        self.events.append("mt warm")
        self.drafts.append((text, src, tgt))
        if self.fail:
            raise RuntimeError("vulkan device lost")

    def translate(self, text, src, tgt):  # the warm-up must never use the stateful path
        self.translated.append((text, src, tgt))


def test_live_sessions_warm_up_once_files_do_not():
    mic, paced, file = Mt(), Mt(), Mt()
    assert cli.warm_live_mt(mic, ("ru", "zh"), None, False) is True
    assert cli.warm_live_mt(paced, ("ru", "zh"), "a.wav", True) is True
    assert cli.warm_live_mt(file, ("ru", "zh"), "a.wav", False) is False
    assert mic.drafts == [(cli.MT_WARM_TEXT["ru"], "ru", "zh")] and len(paced.drafts) == 1 and file.drafts == []
    assert mic.translated == paced.translated == []
    assert cli.warm_live_mt(None, ("ru", "zh"), None, False) is False  # --transcript-only: no translator


def test_a_failed_warm_up_is_reported_and_listening_goes_on():
    said = []
    assert cli.warm_live_mt(Mt(fail=True), ("en", "zh"), None, False, said.append) is False
    assert said == ["tlwu: translation warm-up failed (vulkan device lost)"]


def test_warm_pair_follows_the_session_languages():
    assert cli.mt_warm_pair("ru", "zh", None) == ("ru", "zh")
    assert cli.mt_warm_pair("auto", "zh", ["zh", "ja"]) == ("ja", "zh")  # first candidate that is not the target
    assert cli.mt_warm_pair("auto", "zh", None) == ("en", "zh")
    assert cli.mt_warm_pair("auto", "en", None) == ("zh", "en")  # never a same-language no-op
    assert cli.mt_warm_pair("de", "zh", None)[0] == "de" and "de" not in cli.MT_WARM_TEXT  # falls back to the default text


def _run_session(monkeypatch, tmp_path, argv):
    from tinglongwu import asr as asr_mod
    from tinglongwu import device as device_mod
    from tinglongwu import minspec
    from tinglongwu import pipeline as pipeline_mod

    events, status = [], []
    mt = Mt(events)

    class Asr:
        device, live = "cpu", False

        def __init__(self, *a, **k):
            events.append("asr loaded")

    monkeypatch.setattr(minspec, "check_avx2", lambda force=False: None)
    monkeypatch.setattr(device_mod, "resolve_device", lambda compute, warn=None: "cpu")
    monkeypatch.setattr(asr_mod, "FasterWhisperRecognizer", Asr)
    monkeypatch.setattr(cli, "make_translator", lambda *a, **k: mt)
    monkeypatch.setattr(cli, "select_backend", lambda *a, **k: object())
    monkeypatch.setattr(pipeline_mod, "run", lambda *a, **k: events.append("pipeline run"))
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    args = cli.parse_args(["--lang", "ru", "--file", str(tmp_path / "in.wav"), "--model", "tiny",
                           "--models-dir", str(tmp_path), *argv])

    def report(m):
        status.append(m)
        events.append("ready" if m.startswith(cli.READY_STATUS) else m)

    cli.run_session(args, status=report)
    return events, mt


def test_live_session_warms_mt_once_before_ready(monkeypatch, tmp_path):
    events, mt = _run_session(monkeypatch, tmp_path, ["--paced"])
    assert events.count("mt warm") == 1
    assert events.index("mt preload") < events.index("mt warm") < events.index("ready") < events.index("pipeline run")
    assert mt.drafts == [(cli.MT_WARM_TEXT["ru"], "ru", "zh")] and mt.translated == []


def test_unpaced_file_session_skips_the_mt_warm_up(monkeypatch, tmp_path):
    events, mt = _run_session(monkeypatch, tmp_path, [])
    assert "mt warm" not in events and mt.drafts == []


def test_a_failed_warm_up_keeps_the_hy_engine_of_a_ja_router(tmp_path):
    """CodeRabbit Major on #1751: JaRouter._fallback sets hy = None on a draft() error; a warm-up must not do that."""
    from tinglongwu.hymt import JaRouter

    class Boom:
        def draft_calls(self):
            return 0

        def translate(self, text, src, tgt, draft=False):
            raise RuntimeError("vulkan device lost")

    boom = Boom()
    r = JaRouter(None, tmp_path, factory=lambda *a, **k: boom, all_pairs=True, warn=lambda m: None)
    r.hy = boom  # the model file is absent in tmp_path; install the fake engine directly
    said = []
    assert cli.warm_live_mt(r, ("en", "zh"), None, False, said.append) is False
    assert r.hy is boom and said and "warm-up failed" in said[0]
