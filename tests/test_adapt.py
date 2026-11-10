"""#80: lines arrive too late for N lines -> step down one model (MT first, then ASR); step back up when stable."""
import time

import numpy as np
import pytest

from tinglongwu import adapt, cli
from tinglongwu.asr import Recognizer, Transcript
from tinglongwu.models import ModelNotFound
from tinglongwu.pipeline import run
from tinglongwu.segment import Segmenter
from tinglongwu.translate import PivotTranslator, Translation, Translator


def ladders(mt_lower=("opus",), asr_lower=("base", "tiny")):
    mt = adapt.Ladder("translation engine", "hymt", "MT:hymt", [(n, lambda n=n: f"MT:{n}") for n in mt_lower])
    sp = adapt.Ladder("speech model", "small", "ASR:small", [(n, lambda n=n: f"ASR:{n}") for n in asr_lower])
    return mt, sp


def feed(ctl, values):
    return [ctl.observe(v) for v in values]


def test_steps_down_after_n_consecutive_slow_lines_mt_first_then_asr():
    mt, sp = ladders()
    log = []
    ctl = adapt.LatencyController([mt, sp], budget=2.0, n=3, stable=5, log=log.append)
    assert feed(ctl, [2.5, 2.5, 1.0, 2.5, 2.5]) == [None] * 5  # never 3 in a row: no switch
    assert (mt.label, sp.label, log) == ("hymt", "small", [])
    assert feed(ctl, [3.0]) == ["down"]
    assert (mt.current, sp.current) == ("MT:opus", "ASR:small")  # translation engine goes first
    assert len(log) == 1 and "translation engine hymt -> opus" in log[0] and "2 s" in log[0]
    # the 3 lines already queued behind the old model are ignored, then 3 more slow ones -> speech model
    assert feed(ctl, [9, 9, 9]) == [None] * 3 and sp.label == "small"
    assert feed(ctl, [3, 3, 3]) == [None, None, "down"] and sp.current == "ASR:base"
    assert feed(ctl, [9, 9, 9, 3, 3, 3])[-1] == "down" and sp.label == "tiny"
    assert feed(ctl, [9, 9, 9, 3, 3, 3, 3, 3, 3]) == [None] * 9  # bottom of both ladders: nothing left
    assert len(log) == 3  # one line per switch


def test_steps_back_up_after_a_stable_period_in_reverse_order():
    mt, sp = ladders(asr_lower=("base",))
    log = []
    ctl = adapt.LatencyController([mt, sp], budget=2.0, n=2, stable=4, log=log.append)
    feed(ctl, [3, 3, 0, 0, 3, 3])  # down MT, 2 skipped, down ASR
    assert (mt.label, sp.label) == ("opus", "base")
    feed(ctl, [0, 0])  # skipped
    assert feed(ctl, [0.5, 0.5, 1.5, 0.5]) == [None] * 4  # 1.5 s is within budget but not "fast": counter restarts
    assert feed(ctl, [0.5, 0.5, 0.5]) == [None, None, "up"]
    assert (mt.label, sp.label) == ("opus", "small")  # last one down is first one up
    assert "speech model base -> small" in log[-1] and "stepping back up" in log[-1]
    assert feed(ctl, [0, 0, 0.5, 0.5, 0.5, 0.5]) [-1] == "up" and mt.label == "hymt"
    assert feed(ctl, [0, 0] + [0.5] * 12) == [None] * 14  # at the top: stays


def test_failed_step_up_doubles_the_wait_so_a_weak_machine_stops_flapping():
    mt, _ = ladders()
    ctl = adapt.LatencyController([mt], budget=2.0, n=1, stable=3, log=lambda m: None)
    assert feed(ctl, [3]) == ["down"]
    assert feed(ctl, [0, 0.1, 0.1, 0.1]) == [None, None, None, "up"]
    assert feed(ctl, [0, 3]) == [None, "down"] and ctl.stable == 6  # the probe failed
    assert feed(ctl, [0] + [0.1] * 5) == [None] * 6 and mt.label == "opus"
    assert ctl.observe(0.1) == "up"
    for _ in range(20):  # every probe fails right away
        assert feed(ctl, [0, 3]) == [None, "down"]
        assert feed(ctl, [0] + [0.1] * ctl.stable)[-1] == "up"
    assert ctl.stable == adapt.STABLE_MAX  # capped
    # a step up that held for a full stable period is proven: a later slowdown does not double the wait
    feed(ctl, [0] + [0.1] * ctl.stable)
    assert feed(ctl, [3]) == ["down"] and ctl.stable == adapt.STABLE_MAX
    quick = adapt.LatencyController([ladders()[0]], budget=2.0, n=1, stable=3, log=lambda m: None)
    feed(quick, [3, 0, 0.1, 0.1, 0.1, 0, 0.1, 0.1, 0.1, 3])
    assert quick.stable == 3


def test_ladder_drops_steps_whose_model_is_missing():
    def missing():
        raise ModelNotFound("no opus")

    built = []
    lad = adapt.Ladder("speech model", "small", "S", [("base", missing), ("tiny", lambda: built.append(1) or "T")])
    assert built == []  # lower models are only loaded when needed
    assert lad.down() and lad.label == "tiny" and lad.current == "T"
    assert not lad.down() and lad.up() and lad.label == "small" and not lad.up()
    assert lad.down() and built == [1]  # the loaded object is kept for the next time
    only = adapt.Ladder("translation engine", "hymt", "M", [("opus", missing)])
    assert not only.down() and only.label == "hymt"


def test_smaller_whisper_only_offers_installed_faster_sizes():
    assert adapt.smaller_whisper("small", ["tiny", "base", "small", "large-v3"]) == ["base", "tiny"]
    assert adapt.smaller_whisper("large-v3", ["small", "large-v3-turbo"]) == ["large-v3-turbo", "small"]
    assert adapt.smaller_whisper("tiny", ["tiny", "base"]) == [] and adapt.smaller_whisper("custom", ["base"]) == []


class Tone:
    def __init__(self, n):
        self.n = n

    def frames(self):
        for _ in range(self.n):
            yield np.full(16000, 0.1, np.float32)

    def close(self):
        pass


class ASR(Recognizer):
    def __init__(self, tag="small"):
        self.tag, self.n = tag, 0

    def transcribe(self, audio):
        self.n += 1
        return Transcript(f"{self.tag} {self.n}", "ru")


class MT(Translator):
    def __init__(self, tag, delay=0.0, fail=None):
        self.tag, self.delay, self.fail, self.zh_variant = tag, delay, fail, "tw"

    def translate(self, text, src, tgt):
        if self.fail:
            raise self.fail
        time.sleep(self.delay)
        return Translation(None, f"{self.tag}:{text}")


class ListSink:
    def __init__(self):
        self.rows = []

    def write(self, r):
        self.rows.append(r.target)


def test_forced_slow_run_triggers_exactly_one_downgrade_in_the_real_pipeline():
    """#63 'done when': a forced slow run triggers one downgrade. Real pipeline.run, slow fake engine."""
    mt = adapt.Ladder("translation engine", "hymt", MT("slow", 0.15), [("opus", lambda: MT("fast"))])
    log, sink = [], ListSink()
    ctl = adapt.LatencyController([mt], budget=0.1, n=2, stable=1000, log=log.append)
    run(Tone(8), Segmenter(chunk=1.0, use_vad=False), ASR(), adapt.SwitchableTranslator(mt), sink, "zh",
        on_latency=ctl.observe)
    assert len(sink.rows) == 8  # no line lost over the switch
    assert sink.rows[0].startswith("slow:") and sink.rows[1].startswith("slow:")
    assert all(r.startswith("fast:") for r in sink.rows[2:])
    assert len(log) == 1 and "hymt -> opus" in log[0]


def test_fast_run_never_switches_and_latency_is_reported_per_line():
    seen, sink = [], ListSink()
    run(Tone(4), Segmenter(chunk=1.0, use_vad=False), ASR(), MT("ok", 0.05), sink, "zh", on_latency=seen.append)
    assert len(seen) == 4 and all(0.05 <= s < 1.5 for s in seen)  # ASR + queue wait + MT, in seconds


def test_lower_engine_without_the_language_pair_goes_back_up_and_keeps_the_line():
    mt = adapt.Ladder("translation engine", "hymt", MT("top"), [("opus", lambda: MT("x", fail=ModelNotFound("ko-en")))])
    tr = adapt.SwitchableTranslator(mt)
    assert mt.down() and mt.label == "opus"
    assert tr.translate("안녕", "ko", "zh").text == "top:안녕" and mt.label == "hymt" and not mt.down()
    assert tr.zh_variant == "tw"  # other attributes come from the current engine
    with pytest.raises(ModelNotFound):  # the top engine's own error is not swallowed
        adapt.SwitchableTranslator(adapt.Ladder("translation engine", "o", MT("x", fail=ModelNotFound("p")))).translate("a", "ru", "zh")


def test_make_adaptive_builds_ladders_from_what_is_installed(tmp_path):
    for size in ("small", "base", "large-v3"):
        (tmp_path / f"whisper-{size}").mkdir()
    a = cli.parse_args(["--lang", "ru", "--model", "small"])
    top = MT("hymt")
    made = []
    asr, tr, ctl = cli.make_adaptive(a, ASR("small"), top, tmp_path, "cpu", log=lambda m: None,
                                     make_asr=lambda s: made.append(s) or ASR(s), make_opus=lambda: MT("opus"))
    assert [lad.kind for lad in ctl.ladders] == ["translation engine", "speech model"]
    assert made == [] and asr.transcribe(None).text == "small 1"
    for _ in range(2):
        feed(ctl, [9] * adapt.N_OVER * 2)
    assert tr.translate("a", "ru", "zh").text == "opus:a" and asr.transcribe(None).text == "base 1" and made == ["base"]
    # already on OPUS and no smaller Whisper installed: nothing to adapt, objects are returned untouched
    a2 = cli.parse_args(["--lang", "ru", "--model", "base"])
    opus, plain = PivotTranslator(tmp_path), ASR("base")
    assert cli.make_adaptive(a2, plain, opus, tmp_path, "cpu") == (plain, opus, None)
    assert cli.parse_args(["--no-adapt"]).no_adapt is True and a.no_adapt is False


def test_real_opus_step_is_skipped_when_opus_is_not_installed(tmp_path):
    a = cli.parse_args(["--lang", "ru", "--model", "tiny"])
    asr, tr, ctl = cli.make_adaptive(a, ASR("tiny"), MT("hymt"), tmp_path, "cpu", log=lambda m: None)
    assert feed(ctl, [9] * 10) == [None] * 10 and tr.translate("a", "ru", "zh").text == "hymt:a"
