"""Pure helpers (no GPU, no audio hardware): sentence splitting, output-device matching, resampling."""
from __future__ import annotations

import re
from math import gcd

import numpy as np

_END = re.compile(r"(?<=[.!?…])\s+|(?<=[。！？])")


def split_sentences(text: str, max_chars: int = 220) -> list[str]:
    """Split translated text into sentences for the TTS; over-long pieces are cut at commas/spaces."""
    out: list[str] = []
    for part in _END.split(text.strip()):
        part = part.strip()
        while len(part) > max_chars:
            cut = max(part.rfind(c, 0, max_chars) for c in (",", ";", ":", " ", "、", "，"))
            cut = cut if cut > 0 else max_chars
            out.append(part[:cut].strip(" ,;:、，"))
            part = part[cut:].strip(" ,;:、，")
        if part:
            out.append(part)
    return [s for s in out if re.search(r"\w", s)]


# #316: Vosk-TTS ru splits words only at , . ? ! ; : " ( ) and space; any other punctuation stays inside the word,
# becomes a "phone" (g2p.convert keeps unknown characters) and the worker fails with KeyError on phoneme_id_map,
# so the utterance was never spoken (e.g. "сказать…"). Rewrite them to marks every engine reads the same way.
_TTS_PUNCT = str.maketrans({"…": "...", "—": ", ", "–": ", ", "«": "", "»": "", "“": "", "”": "", "„": ""})


def tts_text(piece: str) -> str:
    """Text to send to the TTS worker for one piece: ellipsis -> '...', dashes -> comma, typographic quotes dropped."""
    return re.sub(r" {2,}", " ", re.sub(r" +,", ",", piece.translate(_TTS_PUNCT))).strip().lstrip(", ")


_CLAUSE = re.compile(r"[,;:—–，、；：]")  # a cut right after one of these keeps natural prosody
STREAM_FIRST_CHARS = 24  # #207: first TTS piece, cut at a clause mark (else a space): Kokoro int8 i5-13500 8 threads
#                          ~0.3-0.8 s for <= 24 chars vs 0.9-1.0 s for 34-40 (scripts/bench_tts_stream.py)
STREAM_GROW = 3  # static plan only (speech_pieces): each next piece <= 3x the previous one. The live path sizes the
#                  next piece from the measured synthesis speed instead (PiecePlanner.next with a budget)
STREAM_SAFETY = 0.7  # live sizing: plan the next piece to need <= 70 % of the audio still queued ahead of it
STREAM_MIN_CHARS = 12  # live sizing floor: a smaller piece costs mostly per-request overhead


def _cut(part: str, budget: int) -> int:
    """Index to cut `part` at so the head is <= budget chars: the last clause mark in range, else the last space.
    Heads are >= 8 chars (never a 1-3 word stub): with no cut in [8, budget], the first one after 8 (a small budget
    must not turn into the whole rest of the sentence). len(part) when it fits or there is nowhere to cut."""
    if len(part) <= budget:
        return len(part)
    marks = [m.end() for m in _CLAUSE.finditer(part, 0, budget + 1)]
    if marks and marks[-1] >= 8:
        return marks[-1]
    sp = part.rfind(" ", 0, budget + 1)
    if sp >= 8:
        return sp
    later = [i for i in (part.find(" ", 8), next((m.end() for m in _CLAUSE.finditer(part, 8)), -1)) if i > 0]
    return min(later) if later else len(part)


class PiecePlanner:
    """#207: cuts one utterance into TTS requests, in order, one at a time. Pieces never span two sentences, a
    sentence is cut at a clause mark (else a space) to fit the budget, and joined the pieces are the original text
    (minus whitespace at the cuts). next(budget) -> the next piece (None when done); budget None = the static rule
    (first_chars for the first piece, then `grow` times the previous one); a given budget is clamped to
    [min_chars, max_chars]."""

    def __init__(self, text: str, first_chars: int = STREAM_FIRST_CHARS, grow: float = STREAM_GROW,
                 max_chars: int = 220, min_chars: int = STREAM_MIN_CHARS):
        self.sents = split_sentences(text, max_chars)
        self.first_chars, self.grow, self.max_chars, self.min_chars = first_chars, grow, max_chars, min_chars
        self.budget = first_chars
        self.count = 0

    def next(self, budget: int | None = None) -> str | None:
        b = self.budget if budget is None else min(self.max_chars, max(self.min_chars, int(budget)))
        while self.sents:
            sent = self.sents[0]
            i = _cut(sent, int(b))
            head, rest = sent[:i].strip(), sent[i:].strip()
            if rest:
                self.sents[0] = rest
            else:
                self.sents.pop(0)
            if not re.search(r"\w", head):
                continue  # punctuation-only leftover (split_sentences already drops those between sentences)
            if rest and not re.search(r"\w", rest):  # only punctuation left in this sentence: keep it with the head
                head += rest
                self.sents.pop(0)
            self.count += 1
            self.budget = min(self.max_chars, max(self.first_chars, self.grow * len(head)))
            return head
        return None


def speech_pieces(text: str, first_chars: int = STREAM_FIRST_CHARS, grow: float = STREAM_GROW,
                  max_chars: int = 220) -> list[str]:
    """#207: all TTS requests for one utterance with the static rule (PiecePlanner without measurements): the first
    piece is cut at a clause boundary to <= first_chars, so the first audio is out quickly, and each later piece is
    at most `grow` times the previous one."""
    pl, out = PiecePlanner(text, first_chars, grow, max_chars), []
    while (p := pl.next()) is not None:
        out.append(p)
    return out


def next_budget(queued_s: float, chars_per_s: float | None, safety: float = STREAM_SAFETY) -> int | None:
    """#207 live sizing: how many characters the next piece may have so that it is synthesized before the audio
    already queued ahead of it (`queued_s` seconds) has finished playing. None (= static rule) until the synthesis
    speed of this worker is known."""
    if not chars_per_s or chars_per_s <= 0:
        return None
    return int(max(0.0, queued_s) * chars_per_s * safety)  # PiecePlanner raises it to its min_chars


class GapMeter:
    """#207: silence between streamed TTS pieces of one utterance, assuming the player starts each pushed chunk as
    soon as the previous one has ended. push(seconds) returns the gap before this chunk (0.0 for the first)."""

    def __init__(self, clock=None):
        import time

        self.clock = clock or time.perf_counter
        self.end = None
        self.gaps: list[float] = []

    def push(self, seconds: float) -> float:
        now = self.clock()
        gap = 0.0 if self.end is None else max(0.0, now - self.end)
        if self.end is not None:
            self.gaps.append(gap)
        self.end = max(now, self.end or now) + seconds
        return gap

    @property
    def max(self) -> float:
        return round(max(self.gaps, default=0.0), 3)


def match_device(names: list[str], needle: str | None) -> int | None:
    """Index of the first device whose name contains `needle` (case-insensitive); no needle -> None (default)."""
    if not needle:
        return None
    n = needle.casefold()
    for i, name in enumerate(names):
        if n in name.casefold():
            return i
    raise LookupError(f"no output device matching {needle!r}; available: {names}")


def resample(audio: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
    """Polyphase resample of a mono float array; returns float32."""
    audio = np.asarray(audio, dtype=np.float32)
    if sr_in == sr_out or audio.size == 0:
        return audio
    from scipy.signal import resample_poly

    g = gcd(sr_in, sr_out)
    return resample_poly(audio, sr_out // g, sr_in // g).astype(np.float32)


_CJK = re.compile(r"[⺀-鿿　-〿豈-﫿＀-￯]")
RETRY_HINTS = {"ru": "\n(Reply in Russian only. Do not output any Chinese characters.)",
               "en": "\n(Reply in English only. Do not output any Chinese characters.)"}
RETRY_HINT = RETRY_HINTS["ru"]


def has_cjk(text: str) -> bool:
    return bool(_CJK.search(text))


def strip_cjk(text: str) -> str:
    """Last resort: drop CJK characters and tidy the leftover punctuation/spaces."""
    t = _CJK.sub(" ", text)
    t = re.sub(r"\s+([,.;:!?])", r"\1", t)
    return re.sub(r"\s{2,}", " ", t).strip(" ,;")


def translate_ru(translate, zh: str, lang: str = "ru") -> tuple[str, int]:
    """translate(text)->str. Returns (translation, retries): one retry with a stricter hint when CJK leaks, then strip.
    `lang` (ru|en) only picks the hint wording; the function name is historical. `zh` is the source text in
    whatever language (#134: English when the target is zh)."""
    ru = translate(zh)
    if lang in ("ja", "zh") or not has_cjk(ru):  # Japanese/Chinese output legitimately contains CJK: no leak check
        return ru, 0
    ru2 = translate(zh + RETRY_HINTS.get(lang, RETRY_HINT))
    return (ru2 if not has_cjk(ru2) else strip_cjk(ru2 or ru)), 1


class Lat:
    """Per-utterance stage timings in seconds for the speak-mode `lat` line (#132).
    Stages: vad (configured end-of-speech silence, #139; `fixed`, reported with every utterance), asr, mt,
    back (back-translation), tts (MT done -> first audio handed to the output)."""
    STAGES = ("vad", "asr", "mt", "back", "tts")

    def __init__(self, clock=None, fixed: dict | None = None):
        import time

        self.clock = clock or time.perf_counter
        self.fixed = dict(fixed or {})
        self.t: dict[str, float] = {}

    def timed(self, name: str, fn, *a, **k):
        t0 = self.clock()
        try:
            return fn(*a, **k)
        finally:
            self.t[name] = self.t.get(name, 0.0) + self.clock() - t0

    def set(self, name: str, seconds: float) -> None:
        self.t[name] = seconds

    def take(self) -> dict[str, float]:
        """Timings collected so far, rounded; the collector is emptied for the next utterance."""
        out, self.t = {k: round(v, 3) for k, v in {**self.fixed, **self.t}.items()}, {}
        return out


def lat_line(t: dict) -> str:
    """'lat vad=0.50 asr=0.41 mt=0.20 back=- tts=0.35 total=1.46' ('-' = stage did not run; vad only in live
    mic mode; total = end of speech -> first audio, so it excludes back, which never delays the audio)."""
    parts = [f"{k}={t[k]:.2f}" if k in t else f"{k}=-" for k in Lat.STAGES if k != "vad" or k in t]
    total = sum(t.get(k, 0.0) for k in ("vad", "asr", "mt", "tts"))
    return "lat " + " ".join(parts) + f" total={total:.2f}"


def latency_fields(asr: float, mt: float, first_audio, tts_done: float) -> dict:
    """Batch-mode fields (#180), all in seconds from the end of Chinese speech: t_asr, t_mt (asr+mt), t_first_audio
    (None if nothing was spoken) and t_tts_done."""
    return {"t_asr": round(asr, 3), "t_mt": round(asr + mt, 3),
            "t_first_audio": None if first_audio is None else round(asr + mt + first_audio, 3),
            "t_tts_done": round(asr + mt + tts_done, 3)}


def latency_line(f: dict) -> str:
    """'latency asr=0.41s mt=0.61s first_audio=0.96s done=1.20s' ('-' when no audio was produced)."""
    fa = "-" if f["t_first_audio"] is None else f"{f['t_first_audio']}"
    return f"latency asr={f['t_asr']}s mt={f['t_mt']}s first_audio={fa}s done={f['t_tts_done']}s"


def timing_line(t: dict, tts: bool = True) -> str:
    """#186: '   [vad 0.60 | stt 1.10 | mt 0.20 | tts_first 0.80 | total 2.70 s]', all seconds from the end of speech:
    vad = endpoint silence waited before the segment was cut, stt, mt, tts_first = MT done -> first TTS audio ready
    (#207; in confirm mode: ready to send). A stage that did not run shows '-' (and is not in the total);
    tts=False (text mode) leaves tts_first out."""
    names = (("vad", "vad"), ("asr", "stt"), ("mt", "mt")) + ((("tts", "tts_first"),) if tts else ())
    parts = [f"{label} {t[k]:.2f}" if k in t else f"{label} -" for k, label in names]
    total = sum(t.get(k, 0.0) for k, _ in names)
    return f"   [{' | '.join(parts)} | total {total:.2f} s]"


def stream_line(pieces: int, gap_max: float, stats: list) -> str:
    """#207: 'stream pieces=3 gap_max=0.00s rtf=0.28' after each spoken utterance. rtf = worker synthesis time /
    audio length over all pieces ('-' if the worker did not report it); gap_max = longest silence between pieces."""
    gen = sum(float(e.get("gen_s") or 0) for e in stats)
    dur = sum(float(e.get("audio_s") or 0) for e in stats)
    rtf = f"{gen / dur:.2f}" if gen and dur else "-"
    return f"stream pieces={pieces} gap_max={gap_max:.2f}s rtf={rtf}"


def live_endpoint(device: str, override=None, env=None) -> tuple[float, float]:
    """(min_silence, step) seconds for the live Segmenter. The end-of-speech wait is VAD pad + min_silence rounded up
    to the next step. CPU (#186) uses 0.5 / 0.25 with a 0.1 s pad (live_vad_pad): about 0.6 s of real silence ends a
    sentence, measured median wait 0.68 s (was 1.25 s with 0.6 / 0.5 and the 0.4 s pad). `override` (--end-silence)
    or env TLWU_END_SILENCE wins; CUDA keeps the old defaults."""
    import os

    raw = override if override is not None else (os.environ if env is None else env).get("TLWU_END_SILENCE")
    try:
        sil = float(raw) if raw not in (None, "") else None
    except ValueError:
        sil = None
    if sil is not None and sil > 0:
        return sil, (0.5 if device == "cuda" else min(0.25, sil))
    return (0.6, 0.5) if device == "cuda" else (0.5, 0.25)


def live_vad_pad(device: str, env=None) -> float | None:
    """Silero span padding (s) for the live speak Segmenter, or None = library default 0.4 s (CUDA, unchanged).
    The pad is pure waiting before a pause can be seen (the cut still keeps 0.5 s of overlap audio after the speech),
    so CPU uses 0.1 s (#186). Env TLWU_VAD_PAD (seconds, >= 0) overrides on any device."""
    import os

    raw = (os.environ if env is None else env).get("TLWU_VAD_PAD")
    try:
        if raw not in (None, "") and float(raw) >= 0:
            return float(raw)
    except ValueError:
        pass
    return None if device == "cuda" else 0.1


class TimedASR:
    """Wraps an ASR object: transcribe() time lands in `lat` as 'asr' (last call wins: dropped segments are not
    part of the utterance that follows)."""

    def __init__(self, asr, lat: Lat):
        self._asr, self._lat = asr, lat

    def transcribe(self, *a, **k):
        self._lat.t.pop("asr", None)
        return self._lat.timed("asr", self._asr.transcribe, *a, **k)

    def __getattr__(self, name):
        return getattr(self._asr, name)
