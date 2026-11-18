"""Offline VAD replay for the mobile two-way bubble rate (#1479).

Runs the same Silero VAD the phone uses (sherpa-onnx VoiceActivityDetector,
silero_vad.onnx from the mobile model pack) on synthetic but reproducible
clips and prints how many VAD segments (= finals = bubbles) each clip makes
for a given threshold / min-speech pair. No network: every input is a local
path.

Clips (16 kHz, seed 0):
  clean       foreground sentences (ru/en TTS + real LibriSpeech reads), 0.8 s
              gaps, -50 dBFS noise floor. Sentences must all survive.
  answers     short real answers ("Нет.", "Да.", "Yes.", ...) at foreground level.
  bg_words    background filler words ("и", "да", "там", "ну", ...) at -15 dB
              below the foreground, 0.6-2.5 s apart. Ideally 0 bubbles.
  bg_babble   two LibriSpeech reads overlapped at -15 dB below the foreground.
  mixed       clean foreground with bg_words laid over it.

Usage:
  python mobile/tool/vad_replay.py --vad silero_vad.onnx \
      --tts-ru vits-piper-ru_RU-irina-medium --tts-en vits-piper-en_US-amy-low \
      --speech a.ogg b.ogg [--clips DIR] [--threshold 0.5 --min-speech 0.25] [--compare 0.6:0.5]
"""
from __future__ import annotations

import argparse
import os

import numpy as np

SR = 16000
FG_DBFS = -20.0
BG_REL_DB = -15.0
NOISE_DBFS = -50.0
# Same as AsrConfig in mobile/lib/services/asr_worker.dart.
MIN_SILENCE = 0.35
MAX_SPEECH = 6.0
KEEP_COVER = 0.8  # a foreground item counts as kept when VAD segments cover >= 80% of it

RU_SENTENCES = [
    "Добрый день, я хотел бы узнать, как пройти к вокзалу.",
    "Скажите, пожалуйста, сколько стоит этот билет?",
    "Мы приехали вчера вечером и остановились в гостинице.",
    "Можно мне счёт, пожалуйста.",
    "Я не понимаю, повторите ещё раз медленнее.",
    "Завтра утром у нас встреча в девять часов.",
    "Где здесь ближайшая аптека?",
    "Спасибо большое, вы мне очень помогли.",
]
EN_SENTENCES = [
    "Could you tell me where the nearest station is?",
    "I would like to book a table for two people tonight.",
    "The meeting starts at nine tomorrow morning.",
    "Thank you, that was very helpful.",
]
ANSWERS = [("ru", "Нет."), ("ru", "Да."), ("en", "Yes."), ("en", "No."), ("ru", "Хорошо."), ("en", "Okay.")]
FILLERS = ["и", "да", "там", "ну", "вот", "так", "ага", "это"]


def _db(x: np.ndarray) -> float:
    return 20 * np.log10(np.sqrt(np.mean(x**2)) + 1e-12)


def _level(x: np.ndarray, dbfs: float) -> np.ndarray:
    return x * 10 ** ((dbfs - _db(x)) / 20)


def _trim(x: np.ndarray) -> np.ndarray:
    e = np.abs(x)
    idx = np.where(e > e.max() * 0.02)[0]
    return x[idx[0] : idx[-1] + 1] if len(idx) else x


def _resample(x: np.ndarray, sr: int) -> np.ndarray:
    if sr == SR:
        return x
    from scipy.signal import resample_poly

    g = np.gcd(sr, SR)
    return resample_poly(x, SR // g, sr // g)


class Tts:
    def __init__(self, d: str):
        import sherpa_onnx

        onnx = next(f for f in os.listdir(d) if f.endswith(".onnx"))
        cfg = sherpa_onnx.OfflineTtsConfig(
            model=sherpa_onnx.OfflineTtsModelConfig(
                vits=sherpa_onnx.OfflineTtsVitsModelConfig(
                    model=os.path.join(d, onnx),
                    tokens=os.path.join(d, "tokens.txt"),
                    data_dir=os.path.join(d, "espeak-ng-data"),
                ),
                num_threads=2,
            )
        )
        self.tts = sherpa_onnx.OfflineTts(cfg)

    def say(self, text: str) -> np.ndarray:
        a = self.tts.generate(text, sid=0, speed=1.0)
        return _trim(_resample(np.asarray(a.samples, dtype=np.float64), a.sample_rate))


def _place(items, gap_fn, rng, lead=1.0):
    """Concatenate items with gaps; return (signal, [(start, end), ...])."""
    pos = int(lead * SR)
    spans, parts = [], [np.zeros(pos)]
    for x in items:
        spans.append((pos, pos + len(x)))
        parts.append(x)
        g = int(gap_fn(rng) * SR)
        parts.append(np.zeros(g))
        pos += len(x) + g
    parts.append(np.zeros(SR))
    return np.concatenate(parts), spans


def build_clips(tts_ru: Tts, tts_en: Tts, speech: list[str]):
    import soundfile as sf

    rng = np.random.default_rng(0)
    say = {"ru": tts_ru.say, "en": tts_en.say}
    reads = []
    for p in speech:
        a, sr = sf.read(p)
        if a.ndim > 1:
            a = a.mean(axis=1)
        reads.append(_trim(_resample(a, sr)))

    fg = [_level(say["ru"](s), FG_DBFS) for s in RU_SENTENCES]
    fg += [_level(say["en"](s), FG_DBFS) for s in EN_SENTENCES]
    fg += [_level(r, FG_DBFS) for r in reads]
    clean, clean_spans = _place(fg, lambda r: 0.8, rng)

    ans = [_level(say[lang](t), FG_DBFS) for lang, t in ANSWERS]
    answers, ans_spans = _place(ans, lambda r: 1.2, rng)

    bg_level = FG_DBFS + BG_REL_DB
    words = [_level(say["ru"](FILLERS[i % len(FILLERS)]), bg_level) for i in range(32)]
    bg_words, _ = _place(words, lambda r: r.uniform(0.6, 2.5), rng)

    n = max(len(r) for r in reads)
    bed = np.zeros(n)
    for r in reads:
        bed[: len(r)] += r
    bg_babble = np.concatenate([np.zeros(SR), _level(bed, bg_level), np.zeros(SR)])

    m = max(len(clean), len(bg_words))
    mixed = np.zeros(m)
    mixed[: len(clean)] += clean
    mixed[: len(bg_words)] += bg_words

    out = {
        "clean": (clean, clean_spans),
        "answers": (answers, ans_spans),
        "bg_words": (bg_words, []),
        "bg_babble": (bg_babble, []),
        "mixed": (mixed, clean_spans),
    }
    for k, (x, s) in out.items():
        noise = rng.normal(0, 1, len(x))
        out[k] = ((x + _level(noise, NOISE_DBFS)).astype(np.float32), s)
    return out


def save_clips(d: str, clips) -> None:
    import json

    import soundfile as sf

    os.makedirs(d, exist_ok=True)
    for k, (x, _) in clips.items():
        sf.write(os.path.join(d, f"{k}.wav"), x, SR, subtype="FLOAT")
    with open(os.path.join(d, "spans.json"), "w") as f:
        json.dump({k: [list(map(int, sp)) for sp in s] for k, (_, s) in clips.items()}, f)


def load_clips(d: str):
    import json

    import soundfile as sf

    with open(os.path.join(d, "spans.json")) as f:
        spans = json.load(f)
    return {k: (sf.read(os.path.join(d, f"{k}.wav"), dtype="float32")[0], [tuple(s) for s in v]) for k, v in spans.items()}


def run_vad(vad_model: str, x: np.ndarray, threshold: float, min_speech: float):
    import sherpa_onnx

    cfg = sherpa_onnx.VadModelConfig()
    cfg.silero_vad.model = vad_model
    cfg.silero_vad.threshold = threshold
    cfg.silero_vad.min_silence_duration = MIN_SILENCE
    cfg.silero_vad.min_speech_duration = min_speech
    cfg.silero_vad.max_speech_duration = MAX_SPEECH
    cfg.sample_rate = SR
    vad = sherpa_onnx.VoiceActivityDetector(cfg, buffer_size_in_seconds=30)
    segs = []
    step = 1600  # 100 ms mic chunks, like the phone recorder stream
    for i in range(0, len(x), step):
        vad.accept_waveform(x[i : i + step])
        while not vad.empty():
            segs.append((vad.front.start, vad.front.start + len(vad.front.samples)))
            vad.pop()
    vad.flush()
    while not vad.empty():
        segs.append((vad.front.start, vad.front.start + len(vad.front.samples)))
        vad.pop()
    return segs


def kept(spans, segs) -> int:
    k = 0
    for a, b in spans:
        cov = sum(max(0, min(b, e) - max(a, s)) for s, e in segs)
        k += cov >= KEEP_COVER * (b - a)
    return k


def report(vad_model, clips, threshold, min_speech):
    row = {}
    for name, (x, spans) in clips.items():
        segs = run_vad(vad_model, x, threshold, min_speech)
        row[name] = (len(segs), kept(spans, segs), len(spans))
    return row


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--vad", required=True)
    ap.add_argument("--tts-ru")
    ap.add_argument("--tts-en")
    ap.add_argument("--speech", nargs="+")
    ap.add_argument("--clips", help="dir: load clips saved by an earlier run, or save them there")
    ap.add_argument("--threshold", type=float, default=0.5)
    ap.add_argument("--min-speech", type=float, default=0.25)
    ap.add_argument("--compare", nargs="*", default=[], help="extra threshold:min_speech pairs")
    a = ap.parse_args()

    clips = load_clips(a.clips) if a.clips and os.path.exists(os.path.join(a.clips, "spans.json")) else None
    if clips is None:
        clips = build_clips(Tts(a.tts_ru), Tts(a.tts_en), a.speech)
        if a.clips:
            save_clips(a.clips, clips)
    pairs = [(a.threshold, a.min_speech)] + [tuple(map(float, c.split(":"))) for c in a.compare]
    names = list(clips)
    print("threshold min_speech | " + " | ".join(f"{n} bubbles(kept/total)" for n in names))
    for th, ms in pairs:
        row = report(a.vad, clips, th, ms)
        cells = [f"{row[n][0]} ({row[n][1]}/{row[n][2]})" if row[n][2] else f"{row[n][0]}" for n in names]
        print(f"{th:.2f} {ms:.2f} | " + " | ".join(cells))


if __name__ == "__main__":
    main()
