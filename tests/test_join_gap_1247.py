"""#1247 / #894: while live ASR catches up on a backlog, one joined ASR call is one stretch with no new subtitle.
hw-tests run 44248844590 (dev bd2fd71): 28 s joins on the CPU runner left 11.6 s without a subtitle (#894 limit 10 s).
The join is now sized from the measured ASR cost so each call stays near pipeline.ASR_JOIN_CALL_S."""
import numpy as np

from tinglongwu import pipeline
from tinglongwu.asr import Transcript
from tinglongwu.pipeline import join_cap, run

from test_catchup_1369 import SR, Clip, Cpu, OneSeg, Shown, SlowASR, VirtualClock, _heard, _idx_of, _text

FIX, PER_S = 2.0, 0.35  # 3 s -> 3.05 s (slower than real time), 28 s -> 11.8 s (the runner's 11.6 s gap)
STALL_S = 40.0          # one slow first call (model warm-up / CPU spike) builds a backlog like the runner's
CLIP_S, SEG_S = 240.0, 3.0
MAX_GAP_S = 10.0        # #894 Acceptance (1)


class RunnerASR(SlowASR):
    def __init__(self, clock):
        super().__init__(Cpu(), clock)
        self.sizes = []

    def transcribe(self, audio):
        self.clock.sleep(STALL_S if not self.sizes else FIX + PER_S * len(audio) / SR)
        self.sizes.append(len(audio) / SR)
        return Transcript(_text(_idx_of(audio)), "en")


def _simulate(monkeypatch):
    clock = VirtualClock()
    clock.patch(monkeypatch)
    asr = RunnerASR(clock)
    src, sink = Clip(int(CLIP_S / SEG_S), clock), Shown(clock.monotonic)
    stop = clock.Event()
    th = clock.Thread(target=run, args=(src, OneSeg(), asr, None, sink, "en"), kwargs={"stop_event": stop})
    t0 = clock.monotonic()
    th.start()
    clock.sleep(CLIP_S + 60)  # tail: let the backlog drain
    stop.set()
    th.join(5)
    assert not th.is_alive()
    clock.sleep(2 * SEG_S)
    times = [t0 + STALL_S] + [w for w, _ in sink.lines]  # from the end of the stall (no line can exist before it)
    gap = max(b - a for a, b in zip(times, times[1:]))
    return gap, asr.sizes, sink


def test_catch_up_never_leaves_10_s_without_a_subtitle(monkeypatch):
    gap, sizes, sink = _simulate(monkeypatch)
    assert gap <= MAX_GAP_S, (gap, sizes)
    assert _heard(sink) == list(range(int(CLIP_S / SEG_S)))  # nothing dropped (#1263)
    assert max(sizes[1:]) > SEG_S, sizes  # still joins (#1369 throughput)


def test_old_policy_left_more_than_10_s(monkeypatch):
    """The same runner with the pre-#1247 join (always up to ASR_JOIN_MAX_S): the hw-tests failure, 11.45 s here."""
    monkeypatch.setattr(pipeline, "join_cap", lambda history: pipeline.ASR_JOIN_MAX_S)
    gap, sizes, _ = _simulate(monkeypatch)
    assert gap > MAX_GAP_S and max(sizes) > 20.0, (gap, sizes)


def test_join_cap_upper_bound_from_newest_call():
    # 3 s cost 3 s, nothing longer seen: cost <= rate * seconds -> 6 s fits ASR_JOIN_CALL_S
    assert join_cap([(3.0, 3.0)]) == 6.0


def test_join_cap_one_spike_does_not_unlock_28_s():
    # a 40 s spike on a 3 s call, then a 6 s call in 4.1 s: the fit says length is free, growth stays at 2 x 6 s
    assert join_cap([(3.0, 40.0), (6.0, 4.1)]) == 12.0


def test_join_cap_follows_the_fitted_cost():
    hist = [(a, FIX + PER_S * a) for a in (3.0, 6.0, 9.0, 3.0)]
    cap = join_cap(hist)
    assert FIX + PER_S * cap <= pipeline.ASR_JOIN_CALL_S + 1e-6, cap
    assert cap >= 6.0, cap


def test_join_cap_without_history_is_the_old_bound():
    assert join_cap([]) == pipeline.ASR_JOIN_MAX_S
    assert join_cap([(3.0, 0.0)]) == pipeline.ASR_JOIN_MAX_S
