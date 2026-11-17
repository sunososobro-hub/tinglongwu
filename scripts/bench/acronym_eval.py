#!/usr/bin/env python3
"""#1397: do default Whisper hotwords (tinglongwu/data/hotwords-ru.txt) help Russian speech with Latin acronyms?

Dev tool (not part of the offline runtime). Runs on cloud CPU runners next to term_eval.py (`eval-terms.yml`):

  <tts-venv>/bin/python scripts/bench/acronym_eval.py synth --voice DIR --data DATA     # Piper ru (GPL: own venv)
  python scripts/bench/acronym_eval.py run --data DATA --out OUT --models-dir MODELS [--whisper W]
  python scripts/bench/acronym_eval.py report OUT

Set: ACRONYMS below, one self-written Russian sentence per acronym of the default list (38), spoken the way Russian
speakers say it (`say`: letter names, e.g. "эй-пи-ай" for API), plus DISTRACTORS (no acronym, some with words that
sound like one: "ай", "пиар") that count acronyms the hotwords make Whisper insert. Each clip is decoded twice on the
same model: without hotwords (`no`) and with the default list (`hw`), ASR only (the list never reaches MT). Scores:
  acronym accuracy   the acronym is a whole word in the Whisper text
  false insertions   distractor clips whose Whisper text has any acronym of the list
  WER                word error rate over all clips against the written sentence (acronyms in Latin letters)
Synthetic speech: the with/without difference is what this measures, not a field result. The ru WER on the
standard set (eval-langs) is a separate check before the list is turned on (glossary.DEFAULT_HOTWORDS_SHIPPED).
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

# acronym, how a Russian speaker says it, sentence (`{}` = where it goes)
ACRONYMS = [
    ("API", "эй-пи-ай", "Документация к {} лежит в общей папке."),
    ("URL", "ю-эр-эл", "Пришли мне {} этой страницы."),
    ("SQL", "эс-кью-эл", "Этот запрос на {} работает слишком долго."),
    ("JSON", "джейсон", "Сервер отвечает в формате {}."),
    ("HTTP", "эйч-ти-ти-пи", "Старый сервис всё ещё работает по {}."),
    ("HTTPS", "эйч-ти-ти-пи-эс", "Все страницы должны открываться только по {}."),
    ("XML", "экс-эм-эл", "Настройки хранятся в файле {}."),
    ("HTML", "эйч-ти-эм-эл", "Письмо собирается из шаблона {}."),
    ("CSS", "си-эс-эс", "Цвета кнопок задаются в {}."),
    ("CPU", "си-пи-ю", "Нагрузка на {} доходит до ста процентов."),
    ("GPU", "джи-пи-ю", "Модель обучали на одном {}."),
    ("RAM", "рэм", "На ноутбуке всего восемь гигабайт {}."),
    ("SSD", "эс-эс-дэ", "Система стоит на быстром {}."),
    ("USB", "ю-эс-би", "Подключи камеру к порту {}."),
    ("PDF", "пэ-дэ-эф", "Отчёт я пришлю в {} после встречи."),
    ("DNS", "дэ-эн-эс", "Сайт не открывается из-за ошибки {}."),
    ("VPN", "ви-пи-эн", "Без {} внутренний портал недоступен."),
    ("SSH", "эс-эс-аш", "Зайди на сервер по {} и посмотри логи."),
    ("IP", "ай-пи", "Какой {} у этого принтера?"),
    ("TCP", "ти-си-пи", "Соединение по {} рвётся каждые пять минут."),
    ("UDP", "ю-дэ-пэ", "Видео идёт по {}, поэтому кадры иногда теряются."),
    ("SDK", "эс-дэ-ка", "Новую версию {} выпустили на прошлой неделе."),
    ("IDE", "ай-ди-и", "Открой проект в своей {}."),
    ("CLI", "си-эл-ай", "Это можно сделать через {} одной командой."),
    ("LLM", "эл-эл-эм", "Ответы генерирует локальная {}."),
    ("ML", "эм-эл", "Команда {} просит больше данных."),
    ("QA", "кью-эй", "Сборку уже передали в {}."),
    ("CI", "си-ай", "Тесты в {} опять упали."),
    ("CD", "си-ди", "После {} изменения сразу попадают к пользователям."),
    ("PR", "пи-ар", "Посмотри, пожалуйста, мой {} до вечера."),
    ("UI", "ю-ай", "Дизайнер переделал {} главного экрана."),
    ("UX", "ю-экс", "Исследование {} покажет, где люди путаются."),
    ("ID", "ай-ди", "Напиши {} заказа в комментарии."),
    ("CRM", "си-ар-эм", "Все звонки клиентов записываются в {}."),
    ("ERP", "и-ар-пи", "Склад ведётся в {} с прошлого года."),
    ("KPI", "кей-пи-ай", "Главный {} на этот квартал ещё не выбрали."),
    ("FAQ", "эф-эй-кью", "Добавь этот вопрос в {} на сайте."),
    ("SMS", "эс-эм-эс", "Код подтверждения приходит по {}."),
]
DISTRACTORS = [
    "Давайте перенесём встречу на четверг.",
    "Я отправлю отчёт после обеда.",
    "У нас осталось десять минут.",
    "Кто будет вести протокол сегодня?",
    "Бюджет на следующий квартал пока не утверждён.",
    "Пожалуйста, выключите микрофон, когда не говорите.",
    "Ай, я совсем забыл про этот звонок.",
    "Хороший пиар нам сейчас не помешает.",
    "Идея простая, но сроки очень сжатые.",
    "Сидим и ждём ответа от бухгалтерии.",
    "Рамка для фотографии стоит на полке.",
    "Рамы для окон привезут завтра.",
]


def clips() -> list[dict]:
    """Every clip: id, written ref text, the text the TTS speaks, and its acronym (None for distractors)."""
    out = [{"id": f"{t.lower()}-0", "term": t, "ref_text": s.format(t), "say": s.format(say)} for t, say, s in ACRONYMS]
    out += [{"id": f"none-{i}", "term": None, "ref_text": s, "say": s} for i, s in enumerate(DISTRACTORS)]
    return out


def _words(s: str) -> list[str]:
    return re.findall(r"\w+", s.lower().replace("ё", "е"))


def asr_hit(term: str, text: str) -> bool:
    """The acronym is a whole word of the text (HTTP is not a hit in "HTTPS", ID not in "IDE")."""
    return term.lower() in _words(text)


def inserted(text: str) -> list[str]:
    """Acronyms of the list present in a text (for distractor clips: never said)."""
    return [t for t, _, _ in ACRONYMS if asr_hit(t, text)]


def wer_counts(ref: str, hyp: str) -> tuple[int, int]:
    """(word edits, reference words): Levenshtein over lower-cased words."""
    r, h = _words(ref), _words(hyp)
    prev = list(range(len(h) + 1))
    for i, rw in enumerate(r, 1):
        cur = [i] + [0] * len(h)
        for j, hw in enumerate(h, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (rw != hw))
        prev = cur
    return prev[-1], len(r)


# ---------- synth (Piper ru, run with the TTS venv's python) ----------

def cmd_synth(a) -> int:
    import numpy as np
    from piper import PiperVoice

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from term_eval import write_wav

    onnx = next((f for f in sorted(Path(a.voice).rglob("*.onnx")) if Path(str(f) + ".json").exists()), None)
    if onnx is None:
        sys.exit(f"acronym_eval: no Piper voice (*.onnx + .onnx.json) under {a.voice}")
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
    print(f"acronym_eval: {len(rows)} clips ({onnx.name}) -> {data}")
    return 0


# ---------- run (offline: tinglongwu ASR on the CPU) ----------

def run_clips(rows: list[dict], asr, hotwords, load_audio, clock=time.perf_counter) -> list[dict]:
    """Each clip without (`no`) and with (`hw`) the default hotwords on the same model, no previous-text prompt."""
    out = []
    for r in rows:
        x = load_audio(r)
        d = dict(r, audio_s=round(len(x) / SAMPLE_RATE, 3))
        for tag, g in (("no", None), ("hw", hotwords)):
            asr.glossary = g
            if hasattr(asr, "_last"):
                asr._last = ""
            t0 = clock()
            t = asr.transcribe(x)
            d[f"asr_{tag}"] = (t.text if t else "").strip()
            d[f"asr_{tag}_s"] = round(clock() - t0, 3)
        out.append(d)
    return out


def cmd_run(a) -> int:
    sys.path.insert(0, str(REPO))
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from india_eval import load_audio
    from tinglongwu.asr import FasterWhisperRecognizer
    from tinglongwu.glossary import load_default_hotwords

    data, out = Path(a.data), Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    rows = [json.loads(x) for x in (data / "manifest.jsonl").read_text("utf-8").splitlines() if x.strip()]
    if a.limit:
        rows = rows[: a.limit]
    hw = load_default_hotwords("ru")
    asr = FasterWhisperRecognizer(Path(a.models_dir), a.whisper, "ru", "cpu")
    res = run_clips(rows, asr, hw, lambda r: load_audio(data / r["wav"]))
    with open(out / "acronyms.jsonl", "w", encoding="utf-8", newline="\n") as f:
        for d in res:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
    meta = {"whisper": a.whisper, "cpu": os.cpu_count(), "hotwords": hw.hotwords()}
    (out / "run.json").write_text(json.dumps(meta, ensure_ascii=False), "utf-8")
    print(f"acronym_eval: {len(res)} clips -> {out}")
    return 0


# ---------- report ----------

def _median(xs):
    xs = sorted(xs)
    if not xs:
        return None
    m = len(xs) // 2
    return round(xs[m] if len(xs) % 2 else (xs[m - 1] + xs[m]) / 2, 3)


def summarize(rows: list[dict]) -> dict:
    term_rows = [r for r in rows if r.get("term")]
    distract = [r for r in rows if not r.get("term")]
    out = {"acronym_clips": len(term_rows), "distractor_clips": len(distract), "per_term": {}}
    for tag in ("no", "hw"):
        edits = words = 0
        for r in rows:
            e, n = wer_counts(r["ref_text"], r[f"asr_{tag}"])
            edits, words = edits + e, words + n
        out[tag] = {
            "acronym_acc": round(sum(asr_hit(r["term"], r[f"asr_{tag}"]) for r in term_rows) / (len(term_rows) or 1), 4),
            "false_insertions": sum(bool(inserted(r[f"asr_{tag}"])) for r in distract),
            "wer": round(edits / (words or 1), 4),
            "asr_median_s": _median([r[f"asr_{tag}_s"] for r in rows]),
        }
    for r in term_rows:
        out["per_term"][r["term"]] = [int(asr_hit(r["term"], r["asr_no"])), int(asr_hit(r["term"], r["asr_hw"]))]
    no, hw = out["no"], out["hw"]
    # #1397 Acceptance: better acronym accuracy, no extra false insertions, WER not worse by more than 0.5 points
    out["helps"] = (hw["acronym_acc"] > no["acronym_acc"] and hw["false_insertions"] <= no["false_insertions"]
                    and hw["wer"] <= no["wer"] + 0.005)
    return out


def table(s: dict) -> str:
    no, hw = s["no"], s["hw"]
    lines = [f"Acronym clips: {s['acronym_clips']}, distractor clips: {s['distractor_clips']}", "",
             "| metric | without hotwords | with default hotwords | change |", "|---|---|---|---|",
             f"| acronym accuracy | {100 * no['acronym_acc']:.1f}% | {100 * hw['acronym_acc']:.1f}% "
             f"| {100 * (hw['acronym_acc'] - no['acronym_acc']):+.1f} pt |",
             f"| false insertions (distractors) | {no['false_insertions']} | {hw['false_insertions']} "
             f"| {hw['false_insertions'] - no['false_insertions']:+d} |",
             f"| WER (all clips) | {100 * no['wer']:.1f}% | {100 * hw['wer']:.1f}% | {100 * (hw['wer'] - no['wer']):+.1f} pt |",
             f"| ASR median s | {no['asr_median_s']} | {hw['asr_median_s']} | |", "",
             f"Default list helps on this set: {'yes' if s['helps'] else 'no'}", "",
             "| acronym | without | with |", "|---|---|---|"]
    lines += [f"| {t} | {a} | {b} |" for t, (a, b) in s["per_term"].items()]
    return "\n".join(lines)


def cmd_report(a) -> int:
    d = Path(a.out)
    rows = [json.loads(x) for x in (d / "acronyms.jsonl").read_text("utf-8").splitlines() if x.strip()]
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
    a = ap.parse_args(argv)
    return {"synth": cmd_synth, "run": cmd_run, "report": cmd_report}[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
