"""Quick translation speed probe (#63 part 2 / #79). Local only, no network, no new dependency.

`recommend()` (hwprobe) decides from sizes alone. On a CPU-only machine that is not enough: Hy-MT2 fits in RAM
almost everywhere but is only pleasant on a fast CPU. Owner rule (#63, 2026-11-10 13:08): the CPU tier uses Hy-MT2 only when it is fast enough
unless Hy-MT2 measures a median below ~1.0 s per sentence (i5-13500: 0.93-0.96 s, "borderline"; office laptops
are expected to be 2-3x slower). So: time a few fixed sentences (about 10-15 s in total, hard budget below),
apply the rule, and remember the answer in the user config until the hardware changes or the user asks again.

Speech model (#131): on the CPU tier the recommended Whisper model is timed on the bundled selftest clip
(`tinglongwu/data/selftest_en.wav`, #90) and stepped down (small -> base -> tiny, installed ones only) while its
real-time factor is above `ASR_CPU_MAX_RTF`. No clip or no installed model = the tier's model, not timed.
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
SLOW_MT = "too-slow"  # verdict: Hy-MT2 is too slow on this CPU (there is no other engine to fall back to)
LEGACY_SLOW_MT = 'opus'  # what speedprobe.json files written before #471 stored for the same verdict
MIN_SAMPLES = 3  # fewer finished sentences than this = no verdict (no verdict)
HYMT_CPU_EXCLUDED_KEY = "hymt2 (CPU)"
# Whisper on the CPU tier: seconds of decoding per second of audio must stay at or below this. 0.5 = 2x headroom,
# because live mode decodes the next chunk while the same CPU translates the previous one (MT up to ~1 s per
# sentence under the owner rule above) and the capture thread keeps running; RTF ~1 would fall behind live audio.
ASR_CPU_MAX_RTF = 0.5
ASR_BUDGET_S = 12.0  # stop timing smaller speech models after this (one clip per model, like BUDGET_S for MT)
CPU_ASR_STEPS = ("large-v3-turbo", "medium", "small", "base", "tiny")  # largest first; only steps down from the tier
CLIP = Path(__file__).resolve().parent / "data" / "selftest_en.wav"
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
    mt: str  # "hymt2" | SLOW_MT: Hy-MT2 on this machine, or "too slow for Hy-MT2 on CPU"
    hymt_median_s: float | None  # measured median seconds per sentence; None = not measured
    samples: int
    note: str  # one line for the user
    cached: bool = False
    asr: str | None = None  # speech model for this machine; None = the tier's own (old cache / not decided)
    asr_rtf: float | None = None  # measured real-time factor of `asr`; None = not timed


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
        return SpeedResult(SLOW_MT, None, len(times),
                           f"speed test too slow or incomplete ({len(times)} sentences): Hy-MT2 stays the engine")
    med = statistics.median(times)
    if med < limit:
        return SpeedResult("hymt2", med, len(times),
                           f"Hy-MT2 on CPU: median {med:.2f} s/sentence (< {limit:.1f} s): using Hy-MT2")
    return SpeedResult(SLOW_MT, med, len(times),
                       f"Hy-MT2 on CPU: median {med:.2f} s/sentence (>= {limit:.1f} s): Hy-MT2 stays the engine")


def time_asr(transcribe, audio, sr: int = 16000, clock=None) -> float:
    """Real-time factor: seconds spent transcribing `audio` / its length in seconds (< 1 = faster than live)."""
    clock = clock or _clock
    t0 = clock()
    transcribe(audio)
    return (clock() - t0) / max(len(audio) / sr, 1e-6)


def _load_clip(path) -> "object | None":
    try:
        import numpy as np
        import soundfile as sf

        audio, sr = sf.read(str(path), dtype="float32", always_2d=False)
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        if sr != 16000:
            n = int(round(len(audio) * 16000 / sr))
            audio = np.interp(np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio).astype("float32")
        return audio if len(audio) else None
    except Exception:  # noqa: BLE001 - no clip (before #90) / unreadable = speech model not timed
        return None


def _whisper_cpu(models_dir, size: str):
    from .asr import FasterWhisperRecognizer

    rec = FasterWhisperRecognizer(Path(models_dir), size=size, lang="en", device="cpu")
    return rec.transcribe


def _asr_steps(tier: Tier, models_dir, installed=None) -> list[str]:
    """Installed speech models the CPU tier may step through, from the tier's own model down."""
    if tier.name != "cpu" or tier.asr not in CPU_ASR_STEPS:
        return []
    if installed is None:
        from .models import whisper_installed

        installed = whisper_installed(Path(models_dir))
    return [m for m in CPU_ASR_STEPS[CPU_ASR_STEPS.index(tier.asr):] if m in installed]


def pick_asr(tier: Tier, models_dir, *, make_asr=None, clip=None, installed=None, clock=None,
             budget_s: float = ASR_BUDGET_S, limit: float = ASR_CPU_MAX_RTF) -> tuple[str, float | None, str]:
    """(speech model, its RTF or None, note). CPU tier only: time the tier's model on the clip, then each smaller
    installed one while the RTF is above `limit` and the budget lasts. Never picks a larger model than the tier's.
    When none is fast enough, the smallest one timed is kept (still the best this machine can do)."""
    if tier.name != "cpu" or tier.asr not in CPU_ASR_STEPS:
        return tier.asr, None, f"speech model {tier.asr} (not timed)"
    steps = _asr_steps(tier, models_dir, installed)
    audio = _load_clip(CLIP if clip is None else clip) if steps else None
    if audio is None:
        why = "no test clip" if steps else "no speech model installed at or below " + tier.asr
        return tier.asr, None, f"speech model {tier.asr} (not timed: {why})"
    clock = clock or _clock
    start, best = clock(), None
    for m in steps:
        try:
            rtf = time_asr((make_asr or _whisper_cpu)(models_dir, m), audio, clock=clock)
        except Exception:  # noqa: BLE001 - a model that does not load is skipped
            continue
        best = (m, rtf)
        if rtf <= limit or clock() - start >= budget_s:
            break
    if best is None:
        return tier.asr, None, f"speech model {tier.asr} (not timed: no model loaded)"
    m, rtf = best
    ok = "real-time" if rtf <= limit else f"slowest-allowed {limit:.1f} not reached"
    return m, rtf, f"speech model {m}: {rtf:.2f}x real time ({ok})"


def signature(hw: HwInfo) -> str:
    """Changes when the hardware does (new GPU, more RAM, another CPU count) -> the cache is ignored."""
    return f"{hw.gpu}|{hw.vram_mib}|{hw.ram_mib}|{hw.cores}"


def cache_path() -> Path:
    from .gui.core import settings_path

    return settings_path().parent / "speedprobe.json"


def load_cached(hw: HwInfo, path=None) -> SpeedResult | None:
    try:
        d = json.loads(Path(path or cache_path()).read_text("utf-8"))
        if d.get("hw") != signature(hw) or d.get("mt") not in ("hymt2", SLOW_MT, LEGACY_SLOW_MT):
            return None
        med, rtf, asr = d.get("hymt_median_s"), d.get("asr_rtf"), d.get("asr")
        mt, note = d["mt"], str(d.get("note", ""))
        if mt == LEGACY_SLOW_MT:  # the old note names the removed engine: say it in Hy-MT2 terms
            mt, note = SLOW_MT, "Hy-MT2 on CPU: too slow on this machine (remembered from an earlier speed test)"
        return SpeedResult(mt, float(med) if med is not None else None, int(d.get("samples", 0)),
                           note, cached=True,
                           asr=asr if asr in CPU_ASR_STEPS else None, asr_rtf=float(rtf) if rtf is not None else None)
    except Exception:  # noqa: BLE001 - missing/corrupt cache = measure again
        return None


def save_cache(hw: HwInfo, r: SpeedResult, path=None) -> bool:
    try:
        p = Path(path or cache_path())
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"hw": signature(hw), "mt": r.mt, "hymt_median_s": r.hymt_median_s,
                                 "samples": r.samples, "note": r.note,
                                 "asr": r.asr, "asr_rtf": r.asr_rtf}, ensure_ascii=False, indent=1), "utf-8")
        return True
    except OSError:
        return False


def _hymt_cpu(models_dir, src: str, tgt: str):
    from .hymt import HyMtTranslator

    tr = HyMtTranslator(models_dir, device="cpu")
    tr.preload()
    return lambda s: tr.translate(s, src, tgt)


def run(hw: HwInfo, tier: Tier, models_dir, *, force: bool = False, path=None, make=None,
        clock=None, src: str = "en", tgt: str = "zh", make_asr=None, clip=None, installed=None) -> SpeedResult:
    """Cached answer for this hardware, else measure Hy-MT2 on CPU (only when the CPU tier may use it) and the
    speech model (#131), and cache. Never raises: a missing model or a failed load means "no verdict", with the
    reason in `note`."""
    if not force:
        hit = load_cached(hw, path)
        # re-measure a CPU entry whose speech model was never timed once timing is possible (clip from #90
        # installed later, or a Whisper model downloaded since): otherwise the old answer would stick forever
        untimed = tier.name == "cpu" and hit is not None and (hit.asr is None or hit.asr_rtf is None)
        if hit and not (untimed and _asr_steps(tier, models_dir, installed)
                        and Path(CLIP if clip is None else clip).is_file()):
            return hit
    asr, rtf, asr_note = pick_asr(tier, models_dir, make_asr=make_asr, clip=clip, installed=installed, clock=clock)
    if tier.name != "cpu":
        r = decide(tier, [])
    elif HYMT_CPU_EXCLUDED_KEY in tier.excluded:
        r = SpeedResult(SLOW_MT, None, 0, f"Hy-MT2 not tested: {tier.excluded[HYMT_CPU_EXCLUDED_KEY]}: Hy-MT2 stays the engine")
    else:
        try:
            r = decide(tier, time_mt((make or _hymt_cpu)(models_dir, src, tgt), clock=clock))
        except Exception as e:  # noqa: BLE001 - not downloaded, llama-cpp missing, load failure
            # not cached: after `tlwu-setup --hymt` the next run should measure for real
            return SpeedResult(SLOW_MT, None, 0, f"Hy-MT2 not tested ({str(e)[:120]}): Hy-MT2 stays the engine; {asr_note}",
                               asr=asr, asr_rtf=rtf)
    r = replace(r, note=f"{r.note}; {asr_note}", asr=asr, asr_rtf=rtf)
    save_cache(hw, r, path)
    return r


def cpu_choice(hw: HwInfo | None = None, path=None, probe=None) -> SpeedResult | None:
    """The remembered speed test when it chose Hy-MT2 for CPU on this hardware (#131), else None.
    Only reads the cache, never measures, so start-up is never held up. A GPU-tier entry (no CPU median) does not
    count: on a GPU machine that runs on CPU (old driver, --device cpu) Hy-MT2 was never timed on the CPU."""
    try:
        if hw is None:
            from .hwprobe import probe as _probe

            hw = (probe or _probe)()
        r = load_cached(hw, path)
    except Exception:  # noqa: BLE001 - a broken probe/cache only means "no remembered choice"
        return None
    if r is None or r.mt != "hymt2" or r.hymt_median_s is None or r.hymt_median_s >= HYMT_CPU_MAX_MEDIAN_S:
        return None
    return r


def cpu_asr_choice(hw: HwInfo | None = None, path=None, probe=None) -> str | None:
    """The speech model the remembered speed test timed for CPU on this hardware (#131), else None. Only reads
    the cache (never measures). A model that was not actually timed (no clip, GPU tier) does not count."""
    try:
        if hw is None:
            from .hwprobe import probe as _probe

            hw = (probe or _probe)()
        r = load_cached(hw, path)
    except Exception:  # noqa: BLE001
        return None
    return r.asr if r is not None and r.asr is not None and r.asr_rtf is not None else None


def smaller_asr(a: str, b: str | None) -> str:
    """The smaller of two speech models in CPU_ASR_STEPS order (`b` None or unknown = `a`)."""
    if b not in CPU_ASR_STEPS or a not in CPU_ASR_STEPS:
        return a
    return max(a, b, key=CPU_ASR_STEPS.index)


def apply(tier: Tier, r: SpeedResult) -> Tier:
    """The tier with the measured translation choice and (CPU tier, timed) the stepped-down speech model."""
    asr = smaller_asr(tier.asr, r.asr) if tier.name == "cpu" and r.asr_rtf is not None else tier.asr
    if r.mt == tier.mt and asr == tier.asr:
        return tier
    return replace(tier, mt=r.mt, asr=asr, reason=f"{tier.reason}; {r.note}")
