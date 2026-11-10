"""Quick translation speed probe (#63 part 2 / #79). Local only, no network, no new dependency.

`recommend()` (hwprobe) decides from sizes alone. On a CPU-only machine that is not enough: Hy-MT2 fits in RAM
almost everywhere but is only pleasant on a fast CPU. Owner rule (#63, 2026-11-10 13:08): the CPU tier keeps OPUS
unless Hy-MT2 measures a median below ~1.0 s per sentence (i5-13500: 0.93-0.96 s, "borderline"; office laptops
are expected to be 2-3x slower). So: time a few fixed sentences (about 10-15 s in total, hard budget below),
apply the rule, and remember the answer in the user config until the hardware changes or the user asks again.

Not here yet: the Whisper real-time factor on a bundled clip (the clip arrives with `tlwu --selftest`, #90).
"""
from __future__ import annotations

import json
import statistics
import time
from dataclasses import dataclass, replace
from pathlib import Path

from .hwprobe import HwInfo, Tier

HYMT_CPU_MAX_MEDIAN_S = 1.0  # owner rule, #63 comment 2026-11-10 13:08
BUDGET_S = 12.0  # stop timing new sentences after this (issue: 10-15 s)
MIN_SAMPLES = 3  # fewer finished sentences than this = no verdict (keep OPUS)
HYMT_CPU_EXCLUDED_KEY = "hymt2 (CPU)"
_clock = time.perf_counter

# Ordinary meeting-style sentences, 9-17 words: the length the live pipeline sends per segment.
SENTENCES = (
    "Good morning everyone, thank you for joining the call on such short notice.",
    "We need to finish the report before the end of this week.",
    "Could you share your screen so that we can all see the numbers?",
    "The delivery was delayed because of a problem at the warehouse.",
    "I think we should test this on a slower machine before the release.",
    "Please send me the updated schedule after the meeting.",
    "Our customers asked for a simpler way to install the program.",
    "Let us take a ten minute break and continue after that.",
)


@dataclass(frozen=True)
class SpeedResult:
    mt: str  # "hymt2" | "opus": what to use on this machine
    hymt_median_s: float | None  # measured median seconds per sentence; None = not measured
    samples: int
    note: str  # one line for the user
    cached: bool = False


def time_mt(translate, sentences=SENTENCES, clock=None, budget_s: float = BUDGET_S) -> list[float]:
    """Seconds per sentence. The first sentence is a warm-up (model load, caches) and is not counted; stops
    starting new sentences once `budget_s` is used up, so a slow machine is not held for minutes."""
    clock = clock or _clock
    start, times = clock(), []
    for i, s in enumerate(sentences):
        t0 = clock()
        translate(s)
        t1 = clock()
        if i:
            times.append(t1 - t0)
        if t1 - start >= budget_s:
            break
    return times


def decide(tier: Tier, times: list[float], limit: float = HYMT_CPU_MAX_MEDIAN_S) -> SpeedResult:
    """Owner rule: CPU tier -> Hy-MT2 only when its median is under `limit`; GPU tier is not second-guessed."""
    if tier.name != "cpu":
        return SpeedResult(tier.mt, None, 0, f"{tier.name.upper()} tier: {tier.mt} (no CPU speed test needed)")
    if len(times) < MIN_SAMPLES:
        return SpeedResult("opus", None, len(times),
                           f"speed test too slow or incomplete ({len(times)} sentences): keeping OPUS")
    med = statistics.median(times)
    if med < limit:
        return SpeedResult("hymt2", med, len(times),
                           f"Hy-MT2 on CPU: median {med:.2f} s/sentence (< {limit:.1f} s): using Hy-MT2")
    return SpeedResult("opus", med, len(times),
                       f"Hy-MT2 on CPU: median {med:.2f} s/sentence (>= {limit:.1f} s): keeping OPUS")


def signature(hw: HwInfo) -> str:
    """Changes when the hardware does (new GPU, more RAM, another CPU count) -> the cache is ignored."""
    return f"{hw.gpu}|{hw.vram_mib}|{hw.ram_mib}|{hw.cores}"


def cache_path() -> Path:
    from .gui.core import settings_path

    return settings_path().parent / "speedprobe.json"


def load_cached(hw: HwInfo, path=None) -> SpeedResult | None:
    try:
        d = json.loads(Path(path or cache_path()).read_text("utf-8"))
        if d.get("hw") != signature(hw) or d.get("mt") not in ("hymt2", "opus"):
            return None
        med = d.get("hymt_median_s")
        return SpeedResult(d["mt"], float(med) if med is not None else None, int(d.get("samples", 0)),
                           str(d.get("note", "")), cached=True)
    except Exception:  # noqa: BLE001 - missing/corrupt cache = measure again
        return None


def save_cache(hw: HwInfo, r: SpeedResult, path=None) -> bool:
    try:
        p = Path(path or cache_path())
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"hw": signature(hw), "mt": r.mt, "hymt_median_s": r.hymt_median_s,
                                 "samples": r.samples, "note": r.note}, ensure_ascii=False, indent=1), "utf-8")
        return True
    except OSError:
        return False


def _hymt_cpu(models_dir, src: str, tgt: str):
    from .hymt import HyMtTranslator

    tr = HyMtTranslator(models_dir, device="cpu")
    tr.preload()
    return lambda s: tr.translate(s, src, tgt)


def run(hw: HwInfo, tier: Tier, models_dir, *, force: bool = False, path=None, make=None,
        clock=None, src: str = "en", tgt: str = "zh") -> SpeedResult:
    """Cached answer for this hardware, else measure Hy-MT2 on CPU (only when the CPU tier may use it) and
    cache. Never raises: a missing model or a failed load means "keep OPUS", with the reason in `note`."""
    if not force:
        hit = load_cached(hw, path)
        if hit:
            return hit
    if tier.name != "cpu":
        r = decide(tier, [])
    elif HYMT_CPU_EXCLUDED_KEY in tier.excluded:
        r = SpeedResult("opus", None, 0, f"Hy-MT2 not tested: {tier.excluded[HYMT_CPU_EXCLUDED_KEY]}: keeping OPUS")
    else:
        try:
            r = decide(tier, time_mt((make or _hymt_cpu)(models_dir, src, tgt), clock=clock))
        except Exception as e:  # noqa: BLE001 - not downloaded, llama-cpp missing, load failure
            # not cached: after `tlwu-setup --hymt` the next run should measure for real
            return SpeedResult("opus", None, 0, f"Hy-MT2 not tested ({str(e)[:120]}): keeping OPUS")
    save_cache(hw, r, path)
    return r


def apply(tier: Tier, r: SpeedResult) -> Tier:
    """The tier with the measured translation choice."""
    return tier if r.mt == tier.mt else replace(tier, mt=r.mt, reason=f"{tier.reason}; {r.note}")
