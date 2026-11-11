"""Smoke run for the eval harness (#57 part 2, #101): a few clips per focus language, ASR -> MT (-> TTS),
per-stage time, peak RAM/VRAM, PASS/FAIL per stage. Offline; reads a local manifest, downloads nothing.

Manifest: `<data>/manifest.jsonl`, one JSON object per line:
  {"id": "en_001", "lang": "en", "wav": "en_001.wav", "ref_text": "...", "ref_zh": "..."}
`ref_<target>` is the reference translation (`ref_zh` for en/ja/ru clips, `ref_en` for zh clips).

Engines are injected (`Engines`), so CI runs the whole thing on a tiny fixture with fakes.
"""
from __future__ import annotations

import json
import statistics
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import scoring

FOCUS_PAIRS = (("en", "zh"), ("ja", "zh"), ("ru", "zh"), ("zh", "en"))  # owner spec v2 on #57: en/ja/ru <-> zh
CLIPS_PER_LANG = 5  # owner spec v2 on #57: "5 clips per focus language"

# PASS/FAIL thresholds (speed only: quality is scored 0-100 by scoring.py, not gated here).
ASR_RTF_MAX = 1.0  # real time by definition: recognising a clip must not take longer than the clip lasts
MT_MEDIAN_MAX_S = 1.0  # owner rule on #63 (2026-11-10): translation is real-time when median < ~1.0 s/sentence
TTS_RTF_MAX = 1.0  # same definition as ASR: speech must be produced faster than it plays
LANG_BUDGET_S = 120.0  # owner spec v2 on #57: "target < 2 min per language"
TOTAL_BUDGET_S = 300.0  # owner spec v2 on #57: "< 5 min total on CPU-only laptop"


@dataclass
class Engines:
    """load_asr(lang) -> object with .transcribe(float32 16 kHz audio) -> Transcript-like (.text, .lang) or None;
    load_mt() -> object with .translate(text, src, tgt) -> Translation-like (.text);
    tts(text, lang) -> seconds of audio produced, or None when no TTS is installed (stage is skipped);
    read_audio(path) -> (float32 mono 16 kHz samples, duration in seconds)."""
    load_asr: Callable
    load_mt: Callable
    tts: Callable | None = None
    read_audio: Callable | None = None


def load_manifest(data_dir) -> list[dict]:
    path = Path(data_dir) / "manifest.jsonl"
    if not path.is_file():
        raise FileNotFoundError(f"no eval data: {path} is missing (one JSON object per line: id, lang, wav, "
                                "ref_text, ref_zh / ref_en)")
    rows = []
    for n, line in enumerate(path.read_text("utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        missing = [k for k in ("id", "lang", "wav", "ref_text") if not row.get(k)]
        if missing:
            raise ValueError(f"{path}:{n}: missing {', '.join(missing)}")
        row["wav"] = str(Path(data_dir) / row["wav"])
        rows.append(row)
    return rows


def load_extra(data_dir) -> list[dict]:
    """The external NHK ja group (#119): [] when the folder or its manifest is absent (copyrighted, never in the repo)."""
    if not data_dir or not (Path(data_dir) / "manifest.jsonl").is_file():
        return []
    return [r for r in load_manifest(data_dir) if r["lang"] == "ja"]


def read_wav(path):
    from tinglongwu.capture.base import SAMPLE_RATE
    from tinglongwu.capture.file import FileSource

    audio = FileSource(path)._load()
    return audio, len(audio) / SAMPLE_RATE


def ram_mib() -> float | None:
    """Resident memory of this process now (psutil when present, else the peak from `resource`, else None)."""
    try:
        import psutil

        return psutil.Process().memory_info().rss / 2 ** 20
    except Exception:  # noqa: BLE001 - psutil is a dev extra, optional at run time
        pass
    try:
        import resource
        import sys

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return peak / 2 ** 20 if sys.platform == "darwin" else peak / 1024
    except Exception:  # noqa: BLE001 - Windows without psutil
        return None


def vram_mib(run=subprocess.run) -> float | None:
    """VRAM in use on GPU 0 now (nvidia-smi); None without an NVIDIA driver."""
    try:
        out = run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                  capture_output=True, text=True, timeout=10).stdout
        return float(out.strip().splitlines()[0])
    except Exception:  # noqa: BLE001
        return None


class _Peak:
    def __init__(self, read):
        self.read, self.value = read, None

    def sample(self):
        v = self.read()
        if v is not None and (self.value is None or v > self.value):
            self.value = v


def _stage(ok: bool | None) -> str:
    return "SKIP" if ok is None else "PASS" if ok else "FAIL"


def run_lang(rows, src, tgt, eng: Engines, mt, clock, peaks, label=None) -> dict:
    t_lang = clock()
    asr = eng.load_asr(src)
    if hasattr(mt, "preload"):
        mt.preload(src, tgt)
    load_s = clock() - t_lang
    read = eng.read_audio or read_wav
    clips, audio_s, asr_s, mt_times, tts_s, tts_audio_s = [], 0.0, 0.0, [], 0.0, 0.0
    for row in rows:
        audio, dur = read(row["wav"])
        audio_s += dur
        t0 = clock()
        tr = asr.transcribe(audio)
        t1 = clock()
        text = (getattr(tr, "text", "") or "").strip()
        out = mt.translate(text, src, tgt).text.strip() if text else ""
        t2 = clock()
        asr_s += t1 - t0
        mt_times.append(t2 - t1)
        if eng.tts is not None and out:
            spoken = eng.tts(out, tgt)
            tts_s += clock() - t2
            tts_audio_s += spoken or 0.0
        for p in peaks:
            p.sample()
        clips.append({"id": row["id"], "ref_text": row["ref_text"], "asr": text, "detected": getattr(tr, "lang", None),
                      "ref_mt": row.get(f"ref_{tgt}"), "mt": out, "latency_s": round(t2 - t0, 3)})
    asr_rtf = asr_s / audio_s if audio_s else None
    mt_median = statistics.median(mt_times)
    tts_rtf = tts_s / tts_audio_s if tts_audio_s else None
    elapsed = clock() - t_lang
    mt_pairs = [(c["ref_mt"], c["mt"]) for c in clips if c["ref_mt"]]
    kana = scoring.kana_rate([c["mt"] for c in clips]) if tgt == "zh" else None
    return {
        "pair": label or f"{src}->{tgt}", "clips": clips, "audio_s": round(audio_s, 2), "elapsed_s": round(elapsed, 2),
        "seconds": {"load": round(load_s, 3), "asr": round(asr_s, 3), "mt": round(sum(mt_times), 3),
                    "tts": round(tts_s, 3) if eng.tts is not None else None},
        "asr_rtf": None if asr_rtf is None else round(asr_rtf, 3), "mt_median_s": round(mt_median, 3),
        "tts_rtf": None if tts_rtf is None else round(tts_rtf, 3),
        # #119: kana in zh output = untranslated Japanese; share x100 (2 decimals), any > 0 is red
        "kana_rate": kana,
        "error_rate": round(scoring.error_rate([(c["ref_text"], c["asr"]) for c in clips], src), 4),
        "chrf": round(scoring.chrf(mt_pairs), 2) if mt_pairs else None,
        "stages": {
            # an empty transcript / translation is a failure whatever the speed
            "asr": _stage(asr_rtf is not None and asr_rtf <= ASR_RTF_MAX and all(c["asr"] for c in clips)),
            "mt": _stage(mt_median <= MT_MEDIAN_MAX_S and all(c["mt"] for c in clips)),
            "tts": _stage(None if eng.tts is None else tts_rtf is not None and tts_rtf <= TTS_RTF_MAX),
            "time": _stage(elapsed <= LANG_BUDGET_S),
            "kana": _stage(None if kana is None else scoring.kana_colour(kana) != "red"),
        },
    }


def run_smoke(rows, eng: Engines, pairs=FOCUS_PAIRS, per_lang=CLIPS_PER_LANG, clock=time.perf_counter,
              ram=ram_mib, vram=vram_mib, totals=None, ja_extra=None) -> dict:
    """Run the smoke set and return the report dict. totals: (RAM MiB, VRAM MiB or None) of this machine.
    ja_extra: ja clips from outside the repo (NHK, #119), run as an extra "ja->zh[nhk]" group; any kana in its zh
    output turns the smoke red."""
    t0 = clock()
    peak_ram, peak_vram = _Peak(ram), _Peak(vram)
    mt = eng.load_mt()
    mt_load_s = clock() - t0
    langs, skipped = [], []
    for src, tgt in pairs:
        mine = [r for r in rows if r["lang"] == src][:per_lang]
        if not mine:
            skipped.append(f"{src}->{tgt}")
            continue
        langs.append(run_lang(mine, src, tgt, eng, mt, clock, (peak_ram, peak_vram)))
    if ja_extra:
        langs.append(run_lang(ja_extra[:per_lang], "ja", "zh", eng, mt, clock, (peak_ram, peak_vram), label="ja->zh[nhk]"))
    if not langs:
        raise ValueError("the manifest has no clip for any focus language (en, ja, ru, zh)")
    total_s = clock() - t0
    clips = [c for lg in langs for c in lg["clips"]]
    total_ram, total_vram = totals or (None, None)
    scores = {
        "asr": statistics.fmean(scoring.asr_score(lg["error_rate"]) for lg in langs),
        "mt": (statistics.fmean(scoring.mt_score(lg["chrf"]) for lg in langs if lg["chrf"] is not None)
               if any(lg["chrf"] is not None for lg in langs) else None),
        "latency": scoring.latency_score([c["latency_s"] for c in clips]),
        "hallucination": None,  # silence/noise clips arrive with the data part (#57 part 3)
        "resources": (scoring.resources_score(peak_ram.value, total_ram, peak_vram.value, total_vram)
                      if peak_ram.value is not None and total_ram else None),
        "lang_id": scoring.lang_id_score([(lg["pair"].split("->")[0], c["detected"]) for lg in langs
                                          for c in lg["clips"]]),
    }
    ok = total_s <= TOTAL_BUDGET_S and all(s != "FAIL" for lg in langs for s in lg["stages"].values())
    return {"mode": "smoke", "pass": ok, "total_s": round(total_s, 2), "mt_load_s": round(mt_load_s, 3),
            "peak_ram_mib": peak_ram.value and round(peak_ram.value), "peak_vram_mib": peak_vram.value and round(peak_vram.value),
            "total_ram_mib": total_ram, "total_vram_mib": total_vram, "languages": langs, "skipped": skipped,
            "thresholds": {"asr_rtf_max": ASR_RTF_MAX, "mt_median_max_s": MT_MEDIAN_MAX_S, "tts_rtf_max": TTS_RTF_MAX,
                           "lang_budget_s": LANG_BUDGET_S, "total_budget_s": TOTAL_BUDGET_S},
            "score": scoring.overall(scores)}
