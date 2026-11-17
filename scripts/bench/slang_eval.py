#!/usr/bin/env python3
"""#1396: does the built-in Russian software slang list (tinglongwu/data/tech-ru.txt) fix those words in ru -> zh?

Same method as the #1257 term eval (scripts/bench/term_eval.py): Piper ru speaks made-up meeting sentences, Whisper
transcribes them once, and Hy-MT2-1.8B (CPU) translates the ASR text and the written text twice on the same loaded
model: without the ru list (`load_builtin("en")`, what a non-Russian source gets) and with it (`load_builtin("ru")`,
what the app uses for a Russian source). A clip scores when the zh output has the list's rendering of its term.

`flores` checks the general FLORES-200 devtest ru -> zh-Hant row: a sentence whose words match no ru entry gets the
same prompt and output either way (glossary.prompt_block / fix_output only act on matching entries), so only the
sentences where the two glossaries differ are translated both ways and scored with chrF (sacrebleu).

    <tts venv>/python scripts/bench/slang_eval.py synth --voice DIR --data DATA
    python scripts/bench/slang_eval.py run --data DATA --out OUT [--whisper large-v3-turbo]
    python scripts/bench/slang_eval.py flores --data FLORES_DIR --out OUT
    python scripts/bench/slang_eval.py report OUT

Sentences are self-written (no owner or user text).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

# (term = the tech-ru.txt entry's first spelling, two sentences with the word as it is said)
SLANG = [
    ("коммит", ["Я сделал коммит перед обедом.", "Последний коммит сломал сборку."]),
    ("прод", ["Мы выкатили это на прод вчера.", "На проде сейчас старая версия."]),
    ("релиз", ["Релиз переносим на пятницу.", "В этот релиз войдут три задачи."]),
    ("баг", ["Я нашёл баг в форме входа.", "Этот баг воспроизводится только на телефоне."]),
    ("фича", ["Новая фича почти готова.", "Эту фичу просили клиенты."]),
    ("ветка", ["Моя ветка отстала от основной.", "Эта ветка больше не нужна."]),
    ("залить", ["Нужно залить файлы на сервер.", "Мы залили новую версию утром."]),
    ("железо", ["Железо для стенда приедет завтра.", "Проблема не в коде, а в железе."]),
    ("деплой", ["Деплой занял двадцать минут.", "После деплоя всё заработало."]),
    ("билд", ["Билд упал на тестах.", "Ночной билд прошёл без ошибок."]),
    ("мердж", ["Мердж лучше сделать после ревью.", "После мерджа появились конфликты."]),
    ("ревью", ["Посмотри, пожалуйста, моё ревью.", "Ревью займёт около часа."]),
    ("откатить", ["Давайте откатить последнее изменение.", "Пришлось откатить обновление."]),
    ("хотфикс", ["Хотфикс уже на сервере.", "Мы выпустим хотфикс сегодня вечером."]),
    ("логи", ["Пришли мне логи с сервера.", "В логах видно ошибку подключения."]),
    ("конфиг", ["Конфиг лежит в общей папке.", "Я поменял конфиг и перезапустил сервис."]),
    ("тикет", ["Заведи тикет на эту проблему.", "Тикет закрыли без ответа."]),
    ("бэклог", ["Эта задача пока в бэклоге.", "Бэклог надо почистить до спринта."]),
    ("костыль", ["Это временный костыль, потом переделаем.", "Без костыля сервис не запускается."]),
    ("легаси", ["Там много легаси кода.", "Легаси модуль никто не хочет трогать."]),
]


def entries():
    """term -> zh rendering from the shipped ru list (the eval follows the file, it never repeats it)."""
    from tinglongwu.glossary import BUILTIN_RU_PATH, parse

    return {e.main: e.target for e in parse(BUILTIN_RU_PATH.read_text(encoding="utf-8-sig"))}


def clips() -> list[dict]:
    out = []
    for term, sents in SLANG:
        for i, s in enumerate(sents):
            out.append({"id": f"{term}-{i}", "term": term, "ref_text": s, "say": s})
    return out


def glossaries():
    """(without, with): the English built-in list alone vs the list a Russian source gets."""
    from tinglongwu.glossary import load_builtin

    return load_builtin("en"), load_builtin("ru")


def zh_hit(target: str, out: str) -> bool:
    return bool(target) and target in out


# ---------- synth (Piper ru, run with the TTS venv's python) ----------

def cmd_synth(a) -> int:
    import numpy as np
    from piper import PiperVoice
    from term_eval import write_wav

    onnx = next((f for f in sorted(Path(a.voice).rglob("*.onnx")) if Path(str(f) + ".json").exists()), None)
    if onnx is None:
        sys.exit(f"slang_eval: no Piper voice (*.onnx + .onnx.json) under {a.voice}")
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
    print(f"slang_eval: {len(rows)} clips ({onnx.name}) -> {data}")
    return 0


# ---------- run ----------

def _mt(tr, g, text: str) -> str:
    tr.glossary = g
    if hasattr(tr, "history"):
        tr.history.clear()
    return tr.translate(text, "ru", "zh").text.strip() if text else ""


def run_clips(rows: list[dict], asr, tr, g_no, g_ru, load_audio) -> list[dict]:
    """ASR once per clip (the list is never a Whisper hint), then MT of the ASR text and the written text with
    and without the ru list."""
    out = []
    for r in rows:
        d = dict(r)
        if hasattr(asr, "_last"):
            asr._last = ""
        t = asr.transcribe(load_audio(r))
        d["asr"] = (t.text if t else "").strip()
        for tag, g in (("no", g_no), ("gl", g_ru)):
            d[f"zh_{tag}"] = _mt(tr, g, d["asr"])
            d[f"zh_gold_{tag}"] = _mt(tr, g, d["ref_text"])
        out.append(d)
    return out


def cmd_run(a) -> int:
    from india_eval import load_audio
    from tinglongwu.asr import FasterWhisperRecognizer
    from tinglongwu.hymt import HyMtTranslator

    data, out = Path(a.data), Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    rows = [json.loads(x) for x in (data / "manifest.jsonl").read_text("utf-8").splitlines() if x.strip()]
    md = Path(a.models_dir)
    asr = FasterWhisperRecognizer(md, a.whisper, "ru", "cpu")
    tr = HyMtTranslator(md, "tw", "cpu")
    tr.preload()
    g_no, g_ru = glossaries()
    res = run_clips(rows, asr, tr, g_no, g_ru, lambda r: load_audio(data / r["wav"]))
    with open(out / "slang.jsonl", "w", encoding="utf-8", newline="\n") as f:
        for d in res:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
    (out / "run.json").write_text(json.dumps({"whisper": a.whisper, "mt": "Hy-MT2-1.8B Q4_K_M CPU",
                                              "cpu": os.cpu_count()}), "utf-8")
    print(f"slang_eval: {len(res)} clips -> {out}")
    return 0


# ---------- flores (general ru row: only sentences the ru list touches can change) ----------

def affected(sentences: list[str], g_no, g_ru) -> list[int]:
    """Indices of sentences whose MT prompt or output fixes differ between the two glossaries."""
    return [i for i, s in enumerate(sentences)
            if g_no.prompt_block(s) != g_ru.prompt_block(s)
            or [e.main for e in g_no.relevant(s)] != [e.main for e in g_ru.relevant(s)]]


def flores_rows(data: Path) -> tuple[list[str], list[str]]:
    from flores_cpu import load_pair

    rows = load_pair(data, "rus_Cyrl")
    return [s for s, _ in rows], [r for _, r in rows]


def cmd_flores(a) -> int:
    from tinglongwu.hymt import HyMtTranslator

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    src, ref = flores_rows(Path(a.data))
    g_no, g_ru = glossaries()
    idx = affected(src, g_no, g_ru)
    tr = HyMtTranslator(Path(a.models_dir), "tw", "cpu")
    tr.preload()
    t0 = time.perf_counter()
    with open(out / "flores.jsonl", "w", encoding="utf-8", newline="\n") as f:
        for i in idx:
            f.write(json.dumps({"i": i, "src": src[i], "ref": ref[i], "no": _mt(tr, g_no, src[i]),
                                "gl": _mt(tr, g_ru, src[i])}, ensure_ascii=False) + "\n")
    (out / "flores.json").write_text(json.dumps({"total": len(src), "affected": len(idx),
                                                 "s": round(time.perf_counter() - t0, 1)}), "utf-8")
    print(f"slang_eval flores: {len(idx)}/{len(src)} ru sentences touched by the ru list -> {out}")
    return 0


# ---------- report ----------

def summarize(rows: list[dict], targets: dict) -> dict:
    n = len(rows) or 1
    s = {"clips": len(rows), "per_term": {}}
    for tag in ("no", "gl"):
        s[tag] = {"zh_acc": round(sum(zh_hit(targets.get(r["term"], ""), r[f"zh_{tag}"]) for r in rows) / n, 4),
                  "zh_gold_acc": round(sum(zh_hit(targets.get(r["term"], ""), r[f"zh_gold_{tag}"])
                                           for r in rows) / n, 4)}
    for r in rows:
        p = s["per_term"].setdefault(r["term"], {"no": 0, "gl": 0, "clips": 0})
        p["clips"] += 1
        for tag in ("no", "gl"):
            p[tag] += zh_hit(targets.get(r["term"], ""), r[f"zh_{tag}"])
    return s


def flores_summary(rows: list[dict], meta: dict, chrf=None) -> dict:
    s = dict(meta)
    if not rows:
        s.update(no=None, gl=None, no_drop=True)
        return s
    if chrf is None:
        from sacrebleu.metrics import CHRF

        def chrf(hyps, refs):
            return CHRF().corpus_score(hyps, [refs]).score
    refs = [r["ref"] for r in rows]
    s["no"] = round(chrf([r["no"] for r in rows], refs), 2)
    s["gl"] = round(chrf([r["gl"] for r in rows], refs), 2)
    s["changed"] = sum(r["no"] != r["gl"] for r in rows)
    s["no_drop"] = s["gl"] >= s["no"]
    return s


def table(s: dict, fl: dict | None) -> str:
    def pct(v):
        return f"{100 * v:.1f}%"

    no, gl = s["no"], s["gl"]
    lines = [f"Clips: {s['clips']} ({len(s['per_term'])} slang terms x 2, Piper ru)", "",
             "| metric | without ru list | with ru list | change |", "|---|---|---|---|",
             f"| zh term accuracy (end to end) | {pct(no['zh_acc'])} | {pct(gl['zh_acc'])} "
             f"| {100 * (gl['zh_acc'] - no['zh_acc']):+.1f} pt |",
             f"| zh term accuracy from written text (MT only) | {pct(no['zh_gold_acc'])} | {pct(gl['zh_gold_acc'])} "
             f"| {100 * (gl['zh_gold_acc'] - no['zh_gold_acc']):+.1f} pt |"]
    if fl is not None:
        lines += ["", f"FLORES-200 devtest ru -> zh-Hant: {fl['affected']}/{fl['total']} sentences touched by the ru "
                      "list (all others are translated identically)."]
        if fl.get("no") is not None:
            lines.append(f"chrF on those sentences: without {fl['no']}, with {fl['gl']} "
                         f"({fl['changed']} outputs changed); no drop: {'yes' if fl['no_drop'] else 'no'}")
    lines += ["", "| term | zh without | zh with |", "|---|---|---|"]
    for term, p in s["per_term"].items():
        lines.append(f"| {term} | {p['no']}/{p['clips']} | {p['gl']}/{p['clips']} |")
    return "\n".join(lines)


def cmd_report(a) -> int:
    d = Path(a.out)
    rows = [json.loads(x) for x in (d / "slang.jsonl").read_text("utf-8").splitlines() if x.strip()]
    s = summarize(rows, entries())
    fl = None
    if (d / "flores.json").exists():
        frows = [json.loads(x) for x in (d / "flores.jsonl").read_text("utf-8").splitlines() if x.strip()]
        fl = flores_summary(frows, json.loads((d / "flores.json").read_text("utf-8")))
    (d / "summary.json").write_text(json.dumps({"terms": s, "flores": fl}, indent=1, ensure_ascii=False), "utf-8")
    print(table(s, fl))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("synth")
    p.add_argument("--voice", required=True)
    p.add_argument("--data", required=True)
    p = sub.add_parser("run")
    p.add_argument("--data", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--whisper", default="large-v3-turbo")
    p.add_argument("--models-dir", default=os.environ.get("TLWU_MODELS", str(Path.home() / "tlwu-models")))
    p = sub.add_parser("flores")
    p.add_argument("--data", required=True, help="flores200_dataset folder (scripts/bench/fetch_data.py)")
    p.add_argument("--out", required=True)
    p.add_argument("--models-dir", default=os.environ.get("TLWU_MODELS", str(Path.home() / "tlwu-models")))
    p = sub.add_parser("report")
    p.add_argument("out")
    a = ap.parse_args(argv)
    return {"synth": cmd_synth, "run": cmd_run, "flores": cmd_flores, "report": cmd_report}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
