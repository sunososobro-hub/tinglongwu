"""#1247 / #894: while live ASR catches up on a backlog, one joined ASR call is one stretch with no new subtitle.
hw-tests run 44248844590 (dev bd2fd71): 28 s joins on the CPU runner left 11.6 s without a subtitle (#894 limit 10 s).
The join is now sized from the measured ASR cost so each call stays near pipeline.ASR_JOIN_CALL_S."""
import pytest

from tinglongwu import pipeline
from tinglongwu.asr import Transcript
from tinglongwu.pipeline import join_cap, run

from test_catchup_1369 import SR, Clip, Cpu, OneSeg, Shown, SlowASR, VirtualClock, _heard, _text, _val

FIX, PER_S = 2.0, 0.35  # 3 s -> 3.05 s (slower than real time), 28 s -> 11.8 s (the runner's 11.6 s gap)
STALL_S = 40.0          # one slow first call (model warm-up / CPU spike) builds a backlog like the runner's
CLIP_S, SEG_S = 240.0, 3.0
MAX_GAP_S = 10.0        # #894 Acceptance (1)


def _ids_in_order(audio):
    """Segment indices in sample order, one per run (a reversed or repeated segment stays visible)."""
    ids = [int(round((v - _val(0)) * 10000)) for v in audio[::SR // 10]]
    return [i for k, i in enumerate(ids) if k == 0 or i != ids[k - 1]]


class RunnerASR(SlowASR):
    def __init__(self, clock):
        super().__init__(Cpu(), clock)
        self.sizes = []

    def transcribe(self, audio):
        self.clock.sleep(STALL_S if not self.sizes else FIX + PER_S * len(audio) / SR)
        self.sizes.append(len(audio) / SR)
        return Transcript(_text(_ids_in_order(audio)), "en")


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


def _cost(fixed, rate, a):
    return fixed + rate * a


def test_join_cap_no_fit_bounds_cost_by_the_newest_call():
    # one length only: a join k x the newest call costs <= k x its cost, so the cap keeps that under ASR_JOIN_HARD_S
    assert join_cap([(3.0, 3.0)]) == 3.0 * pipeline.ASR_JOIN_HARD_S / 3.0
    assert join_cap([(3.0, 40.0)]) < 3.0  # a 40 s spike: one segment next


def test_join_cap_follows_the_fitted_cost():
    hist = [(a, _cost(FIX, PER_S, a)) for a in (3.0, 6.0, 9.0, 3.0)]
    cap = join_cap(hist)
    assert abs(_cost(FIX, PER_S, cap) - pipeline.ASR_JOIN_CALL_S) < 1e-6, cap


def test_join_cap_fit_limits_a_looser_newest_call_estimate():
    # fit ~2 + 0.5 s/s: 8 s takes 6 s; the newest 6 s call in 5 s alone would allow more
    assert abs(join_cap([(3.0, 3.5), (12.0, 8.0), (6.0, 5.0)]) - 8.0) < 0.3


def test_join_cap_after_a_slowdown_trusts_the_newest_call():
    # fit 3 s in 3 s / 12 s in 7.5 s, then a 3 s call took 13 s: one segment next, not the fit's 9 s
    assert join_cap([(3.0, 3.0), (12.0, 7.5), (3.0, 3.0), (3.0, 13.0)]) < 3.0


def test_join_cap_short_tail_does_not_reopen_28_s():
    # a budget-cut join decoded 9 s in 20 s, then its 0.6 s tail in 0.64 s
    assert join_cap([(3.0, 3.1), (9.0, 20.0), (0.6, 0.64)]) <= 9.0


def _drains_and_bounded(fixed, rate, seg=3.0):
    hist = [(seg, _cost(fixed, rate, seg)), (2 * seg, _cost(fixed, rate, 2 * seg))]
    cap = join_cap(hist)
    join = seg * int(cap // seg)  # what catch_up() can take from 3 s segments
    return cap, join, _cost(fixed, rate, join)


@pytest.mark.parametrize("fixed,rate", [(3.0, 0.6), (6.0, 0.1), (4.8, 0.16), (2.0, 0.35)])
def test_join_cap_drains_with_calls_under_the_hard_limit(fixed, rate):
    cap, join, cost = _drains_and_bounded(fixed, rate)
    assert cost <= pipeline.ASR_JOIN_HARD_S + 1e-6, (cap, join, cost)
    assert cost < join, (cap, join, cost)  # the backlog shrinks


def test_join_cap_gap_wins_when_no_join_drains_under_the_limit():
    # 7.6 + 0.15 s/s: draining needs calls over 9 s, so the call stays <= ASR_JOIN_HARD_S
    cap, join, cost = _drains_and_bounded(7.6, 0.15)
    assert _cost(7.6, 0.15, cap) <= pipeline.ASR_JOIN_HARD_S + 1e-6, cap


def test_join_cap_without_history_is_the_old_bound():
    assert join_cap([]) == pipeline.ASR_JOIN_MAX_S
    assert join_cap([(3.0, 0.0)]) == pipeline.ASR_JOIN_MAX_S
