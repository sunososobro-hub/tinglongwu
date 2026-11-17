"""Measures the read-aloud voices of assets/voice_packs.json the way the app runs them
(lib/services/tts_worker.dart: 2 threads, CPU; Supertonic speaker 6, 5 steps), on this machine.

The app's speakLat (end of speech -> start of audio) is ASR final + MT + TTS generation; this
script measures the TTS part, which is the one the voice packs add. Run it on an arm64 board or
the owner's phone (Termux) for device numbers; on CI / a desktop it gives the x86 baseline.

    pip install sherpa-onnx==2.0.1   # same version as mobile/pubspec.yaml
    python3 -I tool/tts_latency.py <WORKDIR> [pack id ...]

WORKDIR holds the unpacked upstream folders (tool/make_packs.py downloads them there).
Prints one Markdown table row per sentence and a summary per pack.
"""
import json
import os
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PACKS_JSON = os.path.join(HERE, "..", "assets", "voice_packs.json")

# Must match TtsVoiceLayout in lib/logic/speech_out.dart and TtsWorker.spawn.
THREADS = 2
SUPERTONIC_SPEAKER = 6
SUPERTONIC_STEPS = 5

SENTENCES = {
    "en": [
        "Nice to meet you.",
        "Could you send me the price list by Friday?",
        "We can ship the first order next month if the samples are approved this week.",
    ],
    "ru": [
        "Приятно познакомиться.",
        "Не могли бы вы прислать прайс-лист до пятницы?",
        "Мы можем отправить первый заказ в следующем месяце, если образцы одобрят на этой неделе.",
    ],
}


def summarize(rows):
    """rows: [(gen_ms, audio_ms)] -> dict with median / max generation time and the real-time factor."""
    if not rows:
        raise ValueError("no measurements")
    gens = [g for g, _ in rows]
    return {
        "n": len(rows),
        "gen_median_ms": round(statistics.median(gens)),
        "gen_max_ms": round(max(gens)),
        "rtf": round(sum(gens) / max(1, sum(a for _, a in rows)), 3),
    }


def pack_files(pack, folder):
    """{app file name: path in the unpacked upstream folder} for one pack (alias = upstream name)."""
    out = {}
    for e in pack["files"]:
        base = e["path"].split("/")[-1]
        src = os.path.join(folder, e.get("alias", base))
        out[base] = src if os.path.exists(src) else os.path.join(folder, base)
    return out


def load_tts(pack, files):
    import sherpa_onnx as so

    if pack.get("kind") == "supertonic":
        model = so.OfflineTtsModelConfig(
            supertonic=so.OfflineTtsSupertonicModelConfig(
                duration_predictor=files["duration_predictor.int8.onnx"],
                text_encoder=files["text_encoder.int8.onnx"],
                vector_estimator=files["vector_estimator.int8.onnx"],
                vocoder=files["vocoder.int8.onnx"],
                tts_json=files["tts.json"],
                unicode_indexer=files["unicode_indexer.bin"],
                voice_style=files["voice.bin"],
            ),
            num_threads=THREADS,
            provider="cpu",
        )
    else:
        model = so.OfflineTtsModelConfig(
            vits=so.OfflineTtsVitsModelConfig(
                model=files["model.onnx"], tokens=files["tokens.txt"], lexicon=files["lexicon.txt"]
            ),
            num_threads=THREADS,
            provider="cpu",
        )
    return so.OfflineTts(so.OfflineTtsConfig(model=model))


def synth(so_tts, pack, text):
    import sherpa_onnx as so

    if pack.get("kind") == "supertonic":
        cfg = so.GenerationConfig()
        cfg.sid = SUPERTONIC_SPEAKER
        cfg.speed = 1.0
        cfg.num_steps = SUPERTONIC_STEPS
        cfg.extra = {"lang": pack["lang"]}
        return so_tts.generate(text, cfg)
    return so_tts.generate(text, sid=0, speed=1.0)


def measure(pack, workdir):
    folder = os.path.join(workdir, pack["source"].split("/")[-1].split(".tar")[0])
    files = pack_files(pack, folder)
    t = time.perf_counter()
    tts = load_tts(pack, files)
    init_ms = (time.perf_counter() - t) * 1000
    synth(tts, pack, SENTENCES[pack["lang"]][0])  # warm-up, as the app's first line after loading
    rows = []
    for text in SENTENCES[pack["lang"]]:
        t = time.perf_counter()
        a = synth(tts, pack, text)
        gen = (time.perf_counter() - t) * 1000
        audio = len(a.samples) * 1000 / a.sample_rate
        rows.append((gen, audio))
        print(f"| {pack['id']} | {len(text)} chars | gen {gen:.0f} ms | audio {audio:.0f} ms |")
    size_mb = sum(e["size"] for e in pack["files"]) / 1e6
    s = summarize(rows)
    print(f"{pack['id']}: size {size_mb:.0f} MB, load {init_ms:.0f} ms, {s}")
    return s


def main(workdir, ids):
    with open(PACKS_JSON, encoding="utf-8") as f:
        packs = [p for p in json.load(f)["packs"] if p["lang"] in SENTENCES and p.get("kind") != "asr"]
    for p in packs:
        if not ids or p["id"] in ids:
            measure(p, workdir)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2:])
