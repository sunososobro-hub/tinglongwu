#!/usr/bin/env python3
"""#1257: does a user glossary (hotwords for Whisper + term list for Hy-MT2) fix technical terms in a ru -> zh meeting?

Dev tool (not part of the offline runtime). Runs on cloud CPU runners (`.github/workflows/eval-terms.yml`):

  <tts-venv>/bin/python scripts/bench/term_eval.py synth --voice DIR --data DATA     # Piper ru (GPL: own venv)
  TLWU_HYMT_TEMP=0 python scripts/bench/term_eval.py run --data DATA --out OUT --models-dir MODELS [--whisper W]
  python scripts/bench/term_eval.py report OUT

Set: TERMS below, 20 terms from the owner's 54-min ru meeting (#1257 owner comment: "Web" heard as "WEP", "PHY" heard
as a common word, "SoC" translated as 賽季, vendor names heard as people's names) plus the protocol / chip vocabulary of
the same kind of meeting. Two Russian sentences per term, spoken by the Piper ru voice the way a Russian engineer
says the term (`say`: Cyrillic letter names, e.g. "эс-о-си" for SoC), plus DISTRACTORS (no term) that count terms
the hotwords make Whisper insert where none was said. Synthetic speech, so the numbers are a lower bound of the
glossary's effect on real voices, not a field result; the with/without difference is what this measures.

Each clip runs twice on the same loaded models: without the glossary (baseline) and with GLOSSARY (= TERMS as a
glossary file: Whisper hotwords + Hy-MT2 terminology prompt + `~` output fixes). Scores:
  ASR term accuracy   the term's Latin spelling (or a listed variant) is in the Whisper text
  zh term accuracy    the zh translation of the ASR text contains the term's zh rendering and none of its known
                      wrong renderings (`~`), i.e. end to end
  zh from gold        the same, translating the written sentence (MT alone)
  false insertions    distractor clips whose Whisper text contains any glossary term
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SAMPLE_RATE = 16000

# term, accepted Latin spellings in the ASR text, zh rendering (Taiwan), known wrong zh renderings, how it is said,
# two sentences (`{}` = where the term goes).
TERMS = [
    ("Web", ["web"], "Web", [], "вэб",
     ["Все настройки меняются через {} интерфейс устройства.", "Через {} консоль это делается за минуту."]),
    ("PHY", ["phy"], "PHY", [], "фай",
     ["Проблема на уровне {}, а не в драйвере.", "Мы заменили {} и пакеты перестали теряться."]),
    ("SoC", ["soc"], "SoC", ["賽季", "季節"], "эс-о-си",
     ["Этот {} сильно греется под нагрузкой.", "В новом {} уже есть аппаратный кодек."]),
    ("Qualcomm", ["qualcomm"], "高通", [], "Квалкомм",
     ["Чип от {} поддерживает оба диапазона.", "Инженеры {} прислали новую прошивку."]),
    ("MediaTek", ["mediatek"], "聯發科", [], "Медиатек",
     ["У {} другая схема питания.", "Мы протестировали плату на {} в прошлом месяце."]),
    ("Broadcom", ["broadcom"], "博通", [], "Бродком",
     ["Коммутатор {} держит полную нагрузку.", "Драйвер для {} пришлось собирать вручную."]),
    ("Realtek", ["realtek"], "瑞昱", [], "Риалтек",
     ["Сетевой контроллер {} стоит почти на всех платах.", "С {} у нас были проблемы с задержкой."]),
    ("Infineon", ["infineon"], "英飛凌", [], "Инфинеон",
     ["Модуль безопасности от {} уже сертифицирован.", "Поставки {} задерживаются на две недели."]),
    ("Nordic", ["nordic"], "Nordic", [], "Нордик",
     ["Для датчиков мы взяли чип {}.", "У {} очень хороший пакет разработчика."]),
    ("MIMO", ["mimo"], "MIMO", [], "МИМО",
     ["Антенны работают в режиме {} четыре на четыре.", "Без {} скорость падает вдвое."]),
    ("OFDM", ["ofdm"], "OFDM", [], "о-эф-дэ-эм",
     ["Модуляция {} устойчива к отражениям.", "Мы сравнили {} с одной несущей."]),
    ("LTE", ["lte"], "LTE", [], "эл-тэ-е",
     ["Резервный канал идёт через {}.", "Модем {} потребляет слишком много."]),
    ("PCIe", ["pcie", "pci-e", "pci express"], "PCIe", [], "пи-си-ай-и",
     ["Карта подключена по шине {}.", "На {} не хватает линий для второго диска."]),
    ("DSP", ["dsp"], "DSP", [], "ди-эс-пи",
     ["Обработка звука идёт на {}.", "Мы перенесли фильтр с процессора на {}."]),
    ("FPGA", ["fpga"], "FPGA", [], "эф-пи-джи-эй",
     ["Прототип сначала собрали на {}.", "Логика на {} работает быстрее."]),
    ("BLE", ["ble"], "BLE", [], "би-эл-и",
     ["Браслет передаёт данные по {}.", "Через {} датчик живёт год от батарейки."]),
    ("Zigbee", ["zigbee"], "Zigbee", [], "зигби",
     ["Лампы в офисе работают по {}.", "Шлюз {} надо перезагрузить."]),
    ("Ethernet", ["ethernet"], "乙太網路", [], "эзернет",
     ["Камеры подключены через {}.", "Порт {} на плате один."]),
    ("U-Boot", ["u-boot", "uboot"], "U-Boot", [], "ю-бут",
     ["Загрузчик {} не видит новую флешку.", "Надо пересобрать {} с другим конфигом."]),
    ("Yocto", ["yocto"], "Yocto", [], "йокто",
     ["Образ системы мы собираем в {}.", "Сборка {} занимает три часа."]),
]
DISTRACTORS = [
    "Давайте перенесём встречу на четверг.",
    "Я отправлю отчёт после обеда.",
    "У нас осталось десять минут.",
    "Кто будет вести протокол сегодня?",
    "Бюджет на следующий квартал пока не утверждён.",
    "Пожалуйста, выключите микрофон, когда не говорите.",
    "Мы прошли мимо этого вопроса в прошлый раз.",
    "Сезон отпусков начинается в июле.",
    "Она написала мне вчера вечером.",
    "Сначала обсудим сроки, потом цену.",
]


def glossary_text() -> str:
    """TERMS as a tlwu glossary file (tinglongwu/glossary.py format): `term = zh ~ wrong | wrong`."""
    lines = ["# #1257 eval glossary: 20 technical terms (ru meeting -> zh)"]
    for term, _, zh, wrong, _, _ in TERMS:
        lines.append(f"{term} = {zh}" + (f" ~ {' | '.join(wrong)}" if wrong else ""))
    return "\n".join(lines) + "\n"


def clips() -> list[dict]:
    """Every clip: id, written ref text, the text the TTS speaks, and its term (None for distractors)."""
    out = []
    for term, _, _, _, say, sents in TERMS:
        for i, s in enumerate(sents):
            out.append({"id": f"{term.lower()}-{i}", "term": term, "ref_text": s.format(term), "say": s.format(say)})
    for i, s in enumerate(DISTRACTORS):
        out.append({"id": f"none-{i}", "term": None, "ref_text": s, "say": s})
    return out


_INFO = {t[0]: t for t in TERMS}


def _norm(s: str) -> str:
    return " " + re.sub(r"[^\w]+", " ", s.lower().replace("‑", "-").replace("-", " ")).strip() + " "


def asr_hit(term: str, text: str) -> bool:
    """The term (any accepted Latin spelling) occurs as whole word(s) in the ASR text."""
    t = _norm(text)
    return any(_norm(v) in t for v in _INFO[term][1])


def zh_hit(term: str, out: str) -> bool:
    """The zh output has the term's rendering and none of its known wrong renderings."""
    _, _, zh, wrong, _, _ = _INFO[term]
    low = out.lower()
    return zh.lower() in low and not any(w in out for w in wrong)


def inserted(text: str) -> list[str]:
    """Glossary terms present in a text (for distractor clips: terms that were never said)."""
    return [t[0] for t in TERMS if asr_hit(t[0], text)]


# ---------- synth (Piper ru, run with the TTS venv's python) ----------

def write_wav(path: Path, samples, sr: int) -> None:
    import wave

    import numpy as np

    x = np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((x * 32767).astype("<i2").tobytes())


def cmd_synth(a) -> int:
    import numpy as np
    from piper import PiperVoice

    onnx = next((f for f in sorted(Path(a.voice).rglob("*.onnx")) if Path(str(f) + ".json").exists()), None)
    if onnx is None:
        sys.exit(f"term_eval: no Piper voice (*.onnx + .onnx.json) under {a.voice}")
    voice = PiperVoice.load(str(onnx), use_cuda=False)
    data = Path(a.data)
    (data / "wav").mkdir(parents=True, exist_ok=True)
    rows = clips()
    with open(data / "manifest.jsonl", "w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            parts, sr = [], 22050
            for ch in voice.synthesize(r["say"]):
                parts.append(np.asarray(ch.audio_float_array, dtype=np.float32).reshape(-1))
                sr = int(ch.sample_rate)
            r["wav"] = f"wav/{r['id']}.wav"
            write_wav(data / r["wav"], np.concatenate(parts) if parts else np.zeros(sr // 2, np.float32), sr)
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    (data / "voice.json").write_text(json.dumps({"voice": onnx.name}), "utf-8")
    print(f"term_eval: {len(rows)} clips ({onnx.name}) -> {data}")
    return 0


# ---------- run (offline: tinglongwu ASR + MT on the CPU) ----------

def run_clips(rows: list[dict], asr, tr, glossary, load_audio, clock=time.perf_counter) -> list[dict]:
    """Each clip without (`no`) and with (`gl`) the glossary on the same models. Translation is ru -> zh with no
    carried context, so one clip's terms never reach another clip's prompt."""
    out = []
    for r in rows:
        x = load_audio(r)
        d = dict(r, audio_s=round(len(x) / SAMPLE_RATE, 3))
        for tag, g in (("no", None), ("gl", glossary)):
            asr.glossary, tr.glossary = g, g
            if hasattr(asr, "_last"):
                asr._last = ""  # no previous-text prompt from the other condition or clip
            t0 = clock()
            t = asr.transcribe(x)
            d[f"asr_{tag}"] = (t.text if t else "").strip()
            d[f"asr_{tag}_s"] = round(clock() - t0, 3)
            for src_field, name in ((f"asr_{tag}", f"zh_{tag}"), ("ref_text", f"zh_gold_{tag}")):
                if hasattr(tr, "history"):
                    tr.history.clear()
                text = d[src_field]
                d[name] = tr.translate(text, "ru", "zh").text.strip() if text else ""
        out.append(d)
    return out


def cmd_run(a) -> int:
    sys.path.insert(0, str(REPO))
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from india_eval import load_audio
    from tinglongwu.asr import FasterWhisperRecognizer
    from tinglongwu.glossary import Glossary, parse
    from tinglongwu.hymt import HyMtTranslator

    data, out = Path(a.data), Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    rows = [json.loads(x) for x in (data / "manifest.jsonl").read_text("utf-8").splitlines() if x.strip()]
    if a.limit:
        rows = rows[: a.limit]
    g = Glossary(parse(glossary_text()))
    (out / "glossary.txt").write_text(glossary_text(), "utf-8")
    md = Path(a.models_dir)
    asr = FasterWhisperRecognizer(md, a.whisper, "ru", "cpu")
    tr = HyMtTranslator(md, "tw", "cpu")
    tr.preload()
    res = run_clips(rows, asr, tr, g, lambda r: load_audio(data / r["wav"]))
    with open(out / "terms.jsonl", "w", encoding="utf-8", newline="\n") as f:
        for d in res:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
    meta = {"whisper": a.whisper, "mt": "Hy-MT2-1.8B Q4_K_M CPU", "cpu": os.cpu_count(), "hotwords": g.hotwords()}
    voice = data / "voice.json"
    if voice.exists():
        meta.update(json.loads(voice.read_text("utf-8")))
    (out / "run.json").write_text(json.dumps(meta, ensure_ascii=False), "utf-8")
    print(f"term_eval: {len(res)} clips -> {out}")
    return 0


# ---------- report ----------

def summarize(rows: list[dict]) -> dict:
    """Per condition (`no` / `gl`): term accuracies over term clips, insertions over distractor clips."""
    term_rows = [r for r in rows if r.get("term")]
    distract = [r for r in rows if not r.get("term")]
    out = {"term_clips": len(term_rows), "distractor_clips": len(distract), "per_term": {}}
    for tag in ("no", "gl"):
        n = len(term_rows) or 1
        out[tag] = {
            "asr_term_acc": round(sum(asr_hit(r["term"], r[f"asr_{tag}"]) for r in term_rows) / n, 4),
            "zh_term_acc": round(sum(zh_hit(r["term"], r[f"zh_{tag}"]) for r in term_rows) / n, 4),
            "zh_gold_term_acc": round(sum(zh_hit(r["term"], r[f"zh_gold_{tag}"]) for r in term_rows) / n, 4),
            "false_insertions": sum(bool(inserted(r[f"asr_{tag}"])) for r in distract),
            "asr_median_s": _median([r[f"asr_{tag}_s"] for r in rows]),
        }
    for r in term_rows:
        p = out["per_term"].setdefault(r["term"], {"asr_no": 0, "asr_gl": 0, "zh_no": 0, "zh_gl": 0, "clips": 0})
        p["clips"] += 1
        for tag in ("no", "gl"):
            p[f"asr_{tag}"] += asr_hit(r["term"], r[f"asr_{tag}"])
            p[f"zh_{tag}"] += zh_hit(r["term"], r[f"zh_{tag}"])
    return out


def _median(xs):
    xs = sorted(xs)
    if not xs:
        return None
    m = len(xs) // 2
    return round(xs[m] if len(xs) % 2 else (xs[m - 1] + xs[m]) / 2, 3)


def table(s: dict) -> str:
    def pct(v):
        return f"{100 * v:.1f}%"

    no, gl = s["no"], s["gl"]
    lines = [f"Term clips: {s['term_clips']} (20 terms x 2), distractor clips: {s['distractor_clips']}", "",
             "| metric | without glossary | with glossary | change |", "|---|---|---|---|"]
    for key, label in (("asr_term_acc", "ASR term accuracy"), ("zh_term_acc", "zh term accuracy (end to end)"),
                       ("zh_gold_term_acc", "zh term accuracy from gold text (MT only)")):
        lines.append(f"| {label} | {pct(no[key])} | {pct(gl[key])} | {100 * (gl[key] - no[key]):+.1f} pt |")
    lines.append(f"| false term insertions (distractors) | {no['false_insertions']} | {gl['false_insertions']} "
                 f"| {gl['false_insertions'] - no['false_insertions']:+d} |")
    lines.append(f"| ASR median s | {no['asr_median_s']} | {gl['asr_median_s']} | |")
    lines += ["", "| term | ASR without | ASR with | zh without | zh with |", "|---|---|---|---|---|"]
    for term, p in s["per_term"].items():
        c = p["clips"]
        lines.append(f"| {term} | {p['asr_no']}/{c} | {p['asr_gl']}/{c} | {p['zh_no']}/{c} | {p['zh_gl']}/{c} |")
    return "\n".join(lines)


def cmd_report(a) -> int:
    d = Path(a.out)
    rows = [json.loads(x) for x in (d / "terms.jsonl").read_text("utf-8").splitlines() if x.strip()]
    s = summarize(rows)
    (d / "summary.json").write_text(json.dumps(s, indent=1, ensure_ascii=False), "utf-8")
    print(table(s))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("synth")
    s.add_argument("--voice", required=True, help="folder with a Piper ru voice (*.onnx + *.onnx.json)")
    s.add_argument("--data", required=True)
    r = sub.add_parser("run")
    r.add_argument("--data", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--models-dir", default=os.environ.get("TLWU_MODELS", ""))
    r.add_argument("--whisper", default="large-v3-turbo")
    r.add_argument("--limit", type=int, default=0, help="first N clips only (smoke)")
    p = sub.add_parser("report")
    p.add_argument("out")
    sub.add_parser("glossary", help="print the eval glossary file")
    a = ap.parse_args(argv)
    if a.cmd == "glossary":
        print(glossary_text(), end="")
        return 0
    return {"synth": cmd_synth, "run": cmd_run, "report": cmd_report}[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
