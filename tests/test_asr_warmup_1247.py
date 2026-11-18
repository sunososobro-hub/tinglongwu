"""#1247 / #894: a live session decodes once before "ready", so the model's first-call setup (seconds on the
hw-tests GPU) is not paid by the first subtitles; and the #894 check prints what happened around its longest gap."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from tinglongwu import asr as asr_mod
from tinglongwu.cli import warm_live_asr

ROOT = Path(__file__).resolve().parent.parent


class FakeModel:
    def __init__(self):
        self.calls = []

    def transcribe(self, audio, **kw):
        self.calls.append((audio, kw))
        pulled = []

        def gen():
            pulled.append(True)
            yield SimpleNamespace(text=" hm", no_speech_prob=0.9, avg_logprob=-2.0, end=1.0)
            yield SimpleNamespace(text="", no_speech_prob=0.9, avg_logprob=-2.0, end=1.0)
            pulled.append("done")

        self.pulled = pulled
        return gen(), SimpleNamespace(language="en")


def _recognizer(lang="en", beam=5):
    r = asr_mod.FasterWhisperRecognizer.__new__(asr_mod.FasterWhisperRecognizer)
    r.model, r.lang, r.beam, r.fast, r.live = FakeModel(), lang, beam, False, False
    r._last, r._tail = "earlier line", None
    return r


def test_warm_up_runs_one_full_decode_without_touching_state():
    r = _recognizer(lang=None, beam=3)
    r.warm_up()
    assert len(r.model.calls) == 1
    audio, kw = r.model.calls[0]
    assert audio.dtype == np.float32 and len(audio) == int(asr_mod.WARMUP_S * asr_mod.SAMPLE_RATE)
    assert float(np.sqrt(np.mean(audio ** 2))) < 0.01  # near-silence, never shown
    assert kw["beam_size"] == 3 and kw["language"] == "en"  # same beam as live decoding; auto skips detection
    assert r.model.pulled[-1:] == ["done"]  # the lazy generator was exhausted (the whole decode really ran)
    assert r._last == "earlier line" and r._tail is None and r.lang is None


class Asr:
    def __init__(self, fail=False):
        self.n, self.fail = 0, fail

    def warm_up(self):
        self.n += 1
        if self.fail:
            raise RuntimeError("cudnn missing")


def test_live_sessions_warm_up_files_do_not():
    mic, paced, file = Asr(), Asr(), Asr()
    assert warm_live_asr(mic, None, False) is True and mic.n == 1
    assert warm_live_asr(paced, "a.wav", True) is True and paced.n == 1
    assert warm_live_asr(file, "a.wav", False) is False and file.n == 0


def test_a_failed_warm_up_is_reported_and_listening_goes_on():
    said = []
    assert warm_live_asr(Asr(fail=True), None, False, said.append) is False
    assert said == ["tlwu: speech model warm-up failed (cudnn missing)"]
    assert warm_live_asr(object(), None, False) is False  # a recognizer without warm_up


def _hl():
    spec = importlib.util.spec_from_file_location("hw_live_checks_warm_1247", ROOT / "scripts" / "hw_live_checks.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_gap_context_names_the_lines_around_the_gap():
    hl = _hl()
    offs = [9.0, 21.0, 37.1, 44.0]
    src = ["one", "two", "three", "four"]
    a, b = hl.gap_span(offs, 0.0, 50.0)
    assert (a, b) == (21.0, 37.1)
    txt = hl.gap_context(offs, src, a, b)
    assert txt == "before: 9.0s 'one'; 21.0s 'two' | after: 37.1s 'three'; 44.0s 'four'"


def test_tlwu_notes_keeps_only_status_lines():
    hl = _hl()
    lines = ["ASR 3 s behind\n", "tlwu: loaded in 2.0 s\n", "PASS offline\n", "tlwu: asr: cut a sentence\n"]
    assert hl.tlwu_notes(lines) == ["tlwu: loaded in 2.0 s", "tlwu: asr: cut a sentence"]
    assert len(hl.tlwu_notes(["tlwu: x\n"] * 50)) == 20
