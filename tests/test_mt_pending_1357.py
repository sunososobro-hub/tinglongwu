"""#1357 (#1342): listen starts with Whisper only while Hy-MT2 downloads; translation joins without a restart."""
import json
import threading
import time

import numpy as np

from tinglongwu import cli
from tinglongwu.asr import Recognizer, Transcript
from tinglongwu.hymt import hymt_path
from tinglongwu.mt_pending import PendingTranslator, hymt_complete, live
from tinglongwu.pipeline import run
from tinglongwu.segment import Segmenter
from tinglongwu.sink import Sink
from tinglongwu.translate import Translation

WORDS = ["один", "два", "три", "четыре", "пять", "шесть"]


class Noise:
    """n one-second chunks of speech-level noise (silence is skipped before ASR)."""

    def __init__(self, n):
        self.n = n

    def frames(self):
        rng = np.random.default_rng(0)
        for _ in range(self.n):
            yield (rng.standard_normal(16000) * 0.1).astype(np.float32)

    def close(self):
        pass


class CountingASR(Recognizer):
    def __init__(self, on_call=None):
        self.n = 0
        self.on_call = on_call

    def transcribe(self, audio):
        self.n += 1
        if self.on_call is not None:
            self.on_call(self.n)
        return Transcript(f"привет номер {WORDS[(self.n - 1) % len(WORDS)]}", "ru")


class Keep(Sink):
    def __init__(self):
        self.lines = []

    def write(self, r):
        self.lines.append(r)


class FakeMT:
    zh_variant = "tw"

    def __init__(self):
        self.preloaded = []
        self.glossary = None

    def translate(self, text, src, tgt):
        return Translation(None, "T:" + text)

    def preload(self, src, tgt):
        self.preloaded.append((src, tgt))


def _run(translator, asr, n=4):
    sink = Keep()
    run(Noise(n), Segmenter(chunk=1.0, use_vad=False), asr, translator, sink, tgt="en", audio_clock=True)
    return sink.lines


def test_start_with_only_whisper_gives_source_lines():
    pending = PendingTranslator(lambda: False, lambda: FakeMT(), poll_s=0)
    lines = _run(pending, CountingASR())
    assert [r.original for r in lines] == [f"привет номер {w}" for w in WORDS[:4]]
    assert all(r.target == "" for r in lines)  # the transcript-only line shape, no exception
    assert not pending.mt_ready() and pending.error is None


def test_model_landing_mid_run_translates_later_lines_without_loss():
    landed = threading.Event()
    built = []

    def build():
        built.append(1)
        return FakeMT()

    pending = PendingTranslator(landed.is_set, build, poll_s=0)

    def on_call(n):
        if n == 3:  # the download finishes while the 3rd segment is in ASR; nobody calls notify()
            landed.set()
        if n == 5:  # the pipeline's own check (MT worker, next line) must have started the load by now
            t0 = time.monotonic()
            while pending._thread is None and time.monotonic() - t0 < 5:
                time.sleep(0.01)
            assert pending.wait(5)

    lines = _run(pending, CountingASR(on_call), n=6)
    src = [f"привет номер {w}" for w in WORDS]
    assert [r.original for r in lines] == src  # every line once, in order
    assert lines[0].target == ""
    assert lines[-1].target == "T:" + src[-1]
    done = [r.target != "" for r in lines]
    assert done == sorted(done)  # source-only lines, then translated lines: never back to source
    assert built == [1]  # loaded once


def test_load_failure_stays_source_only_and_warns_once():
    said = []

    def build():
        raise SystemExit("tlwu: Hy-MT2 failed to load on cpu: boom")

    pending = PendingTranslator(lambda: True, build, poll_s=0, warn=said.append)
    pending.notify()
    pending.wait(5)
    lines = _run(pending, CountingASR())
    assert len(lines) == 4 and all(r.target == "" for r in lines)
    assert len(said) == 1 and "boom" in said[0]
    assert "boom" in pending.error


def test_preload_and_glossary_reach_the_engine_built_later():
    mt = FakeMT()
    pending = PendingTranslator(lambda: True, lambda: mt, poll_s=0)
    pending.glossary = {"tlwu": "tlwu"}
    pending.preload("ru", "en")
    assert pending.translate("x", "ru", "en").text == ""
    pending.notify()
    assert pending.wait(5)
    assert mt.preloaded == [("ru", "en")] and mt.glossary == {"tlwu": "tlwu"}
    assert pending.translate("x", "ru", "en").text == "T:x"
    assert live(pending) is pending and live(mt) is mt


def test_model_file_is_polled_not_on_every_line():
    calls = []
    t = [0.0]
    pending = PendingTranslator(lambda: calls.append(1) or False, FakeMT, poll_s=2.0, clock=lambda: t[0])
    for _ in range(10):
        pending.mt_ready()
    assert len(calls) == 1
    t[0] = 2.5
    pending.mt_ready()
    assert len(calls) == 2


def test_hymt_complete_needs_file_and_manifest(tmp_path):
    assert not hymt_complete(tmp_path)
    gguf = hymt_path(tmp_path)
    gguf.parent.mkdir(parents=True)
    gguf.with_name(gguf.name + ".part").write_bytes(b"x")  # still downloading
    assert not hymt_complete(tmp_path)
    gguf.write_bytes(b"x")
    assert not hymt_complete(tmp_path)  # renamed, manifest not written yet
    (tmp_path / "manifest.json").write_text(json.dumps({gguf.parent.name: {"repo": "r", "revision": "v"}}))
    assert hymt_complete(tmp_path)


def test_cli_default_still_stops_when_hymt_missing(tmp_path):
    try:
        cli.make_translator("hymt2", tmp_path, "tw", "cpu", say=lambda *a, **k: None,
                            choose=lambda *a: ("hymt2", "test"))
    except SystemExit as e:
        assert "tlwu-setup --hymt" in str(e)
    else:
        raise AssertionError("make_translator must stop without the model")


def test_cli_mt_pending_flag_builds_a_pending_translator(tmp_path):
    a = cli.build_parser().parse_args(["--mt-pending"]) if hasattr(cli, "build_parser") else None
    if a is not None:
        assert a.mt_pending
    tr = cli.pending_or_none(tmp_path, "auto", lambda: FakeMT(), zh_variant="tw", warn=lambda m: None)
    assert isinstance(tr, PendingTranslator) and not tr.mt_ready()
    gguf = hymt_path(tmp_path)
    gguf.parent.mkdir(parents=True)
    gguf.write_bytes(b"x")
    (tmp_path / "manifest.json").write_text(json.dumps({gguf.parent.name: {}}))
    assert cli.pending_or_none(tmp_path, "auto", lambda: FakeMT()) is None  # model there: the normal path
    assert cli.pending_or_none(tmp_path, "ollama", lambda: FakeMT()) is None  # other engines are never deferred
