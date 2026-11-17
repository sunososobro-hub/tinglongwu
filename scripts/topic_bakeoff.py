#!/usr/bin/env python
"""#610 / #1378 (#595 part 3): CPU-only bake-off of small instruct GGUF models for offline meeting topic notes.

  python scripts/topic_bakeoff.py --fetch qwen2.5-1.5b,gemma3-1b --dir cand      # bench downloads (never shipped)
  python scripts/topic_bakeoff.py --model qwen2.5-1.5b=cand/qwen2.5-1.5b/x.gguf --busy 2 --out r.json
  python scripts/topic_bakeoff.py --results r1.json r2.json                      # merge saved runs into one table

Every summary goes through the app's own prompt and parser (tinglongwu.topics.build_prompt / parse_summary,
greedy, SUMMARY_TOKENS), so the numbers are what `--topics MODEL.gguf` would produce. Per model it reports:
- s per 5-minute topic: one ~5-minute transcript (about 750 English words) summarized with the app's thread count
  (topics.llama_summarizer: cpus // 4) at low priority while `--busy` processes keep CPU cores busy like ASR + MT;
- peak RAM: the process's peak RSS over the whole run minus the RSS before the model was loaded;
- quality on 25 fixed segments (10 zh, 10 en, 5 ru): key facts present (rubric), invented numbers (a number in the
  notes that is not in the transcript), and output language = transcript language (script share of the notes);
- the subtitle-probe p50 delta while the summarizer runs (#610).
A model qualifies when the 5-minute topic takes < 60 s, peak RAM < 3 GB and the subtitle p50 delta is within +10%;
the default is the qualifying model with the best quality (key facts minus invented numbers, then language), ties
by lower RAM. Not shipped in the app."""
from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing
import os
import re
import statistics
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MAX_P50_DELTA_PCT = 10.0
MAX_TOPIC_S = 60.0  # #1378: a 5-minute topic summarized in under 60 s on 4 vCPU while ASR/MT keep running
MAX_RAM_MB = 3000.0  # #1378: RAM under 3 GB
MIN_LANG_OK = 0.9  # #1378: output language = transcript language (allow 1 slip in 10)
MIN_FACTS = 0.6  # #1378: key points present (3 of every 5 rubric facts, per language)

# Openly downloadable (no login) Q4_K_M instruct GGUFs, small enough for a 4 vCPU / 3 GB budget. Bench only: the
# chosen one gets a pin in tinglongwu_setup/manifest.json, the others are never pinned or shipped.
CANDIDATES = {
    "qwen2.5-1.5b": {"repo": "Qwen/Qwen2.5-1.5B-Instruct-GGUF", "quant": "Q4_K_M", "license": "Apache-2.0"},
    "qwen3-4b-2507": {"repo": "unsloth/Qwen3-4B-Instruct-2507-GGUF", "quant": "Q4_K_M", "license": "Apache-2.0"},
    "gemma3-1b": {"repo": "unsloth/gemma-3-1b-it-GGUF", "quant": "Q4_K_M", "license": "Gemma"},
    "llama3.2-1b": {"repo": "bartowski/Llama-3.2-1B-Instruct-GGUF", "quant": "Q4_K_M", "license": "Llama 3.2"},
    "llama3.2-3b": {"repo": "bartowski/Llama-3.2-3B-Instruct-GGUF", "quant": "Q4_K_M", "license": "Llama 3.2"},
}


def _seg(sid: str, lang: str, lines: list[tuple[str, str]], rubric: list[str]) -> dict:
    return {"id": sid, "lang": lang, "lines": lines, "rubric": rubric}


M, T = "me", "them"
# 25 fixed topic segments; rubric = key facts that must appear in the notes (any of "a|b" counts).
SEGMENTS = [
    _seg("zh-budget", "zh", [(M, "第三季的預算要先確認。"), (T, "行銷預算是三百萬，研發預算是八百萬。"),
                             (T, "研發要在十月底前提交明細。"), (M, "好，我會在週五前把行銷計畫寄給你。")],
         ["三百萬|300萬", "八百萬|800萬", "十月底|10月底", "週五|周五"]),
    _seg("zh-release", "zh", [(T, "新版本預計十一月二十日上線，測試還剩兩個嚴重錯誤。"), (M, "我來修登入錯誤，預計週三完成。"),
                              (T, "我會通知客服團隊更新說明文件。")],
         ["十一月二十日|11月20日", "登入", "週三|周三", "客服"]),
    _seg("zh-office", "zh", [(T, "台北辦公室下個月要搬到信義區，新地址在松仁路。"), (M, "搬家那天是三月八日星期六嗎？"),
                             (T, "對，三月八日。網路線路由資訊部負責。"), (M, "那我負責通知所有員工打包。")],
         ["信義|松仁路", "三月八日|3月8日", "資訊部|网络|網路", "打包|通知"]),
    _seg("zh-client", "zh", [(T, "客戶抱怨上週的報價太高，希望打九折。"), (M, "九折可以，但付款期限要縮短到三十天。"),
                             (T, "我明天會打電話跟他們確認。")],
         ["九折|10%", "三十天|30天", "報價|價格", "明天"]),
    _seg("zh-hiring", "zh", [(M, "我們需要再招兩個前端工程師。"), (T, "人資說年底前只能先招一個。"),
                             (M, "那第一個人優先找有 React 經驗的。"), (T, "我下週一把職缺貼出去。")],
         ["兩個|2個|二", "前端", "一個|1個", "React", "週一|周一"]),
    _seg("zh-server", "zh", [(T, "昨天晚上伺服器當機了四十分鐘，原因是硬碟滿了。"), (M, "要加一個容量超過百分之八十就警告的監控。"),
                             (T, "我今天下午把舊的日誌清掉。")],
         ["四十分鐘|40分鐘", "硬碟|磁碟", "百分之八十|80%", "日誌|紀錄|記錄"]),
    _seg("zh-training", "zh", [(M, "新人訓練改成線上上課好不好？"), (T, "可以，但要錄影，總共六堂課，每堂一小時。"),
                               (M, "那我來準備第一堂的投影片，下週四上課。")],
         ["線上", "錄影", "六堂|6堂", "投影片", "週四|周四"]),
    _seg("zh-supplier", "zh", [(T, "供應商說零件要延後兩週出貨。"), (M, "那生產線會停工，有沒有第二家可以買？"),
                               (T, "有一家在台中，價格貴百分之五。"), (M, "先跟台中那家訂一千個應急。")],
         ["兩週|二週|2週", "台中", "百分之五|5%", "一千|1000|1,000"]),
    _seg("zh-event", "zh", [(T, "尾牙訂在一月十七日，地點在圓山飯店。"), (M, "預算每人一千五百元夠嗎？"),
                            (T, "夠，抽獎獎品由我來買。"), (M, "我負責統計出席人數，下週五前回報。")],
         ["一月十七日|1月17日", "圓山", "一千五百|1500|1,500", "抽獎", "出席"]),
    _seg("zh-security", "zh", [(T, "資安稽核發現有十二台電腦沒有更新防毒軟體。"), (M, "這週內全部要更新完。"),
                               (T, "我會寄信給這十二台電腦的使用者。"), (M, "下次稽核是六月。")],
         ["十二台|12台", "防毒", "這週|本週", "寄信|郵件", "六月|6月"]),
    _seg("en-hiring", "en", [(M, "We need two backend engineers by March."),
                             (T, "The budget allows one hire now and one after the Q2 review."),
                             (M, "I will post the job ad on Monday."),
                             (T, "I will schedule interviews with the first five candidates.")],
         ["two|2", "March", "Q2", "Monday", "five|5"]),
    _seg("en-outage", "en", [(T, "The outage on Tuesday lasted 47 minutes, caused by an expired TLS certificate."),
                             (M, "We should add expiry alerts 30 days ahead."),
                             (T, "I will write the postmortem by Thursday."), (M, "I will set up the alert this week.")],
         ["47", "certificate", "30 days|30-day", "Thursday"]),
    _seg("en-launch", "en", [(T, "The mobile app launch moves from June 3 to June 17 because the payment SDK failed review."),
                             (M, "Can marketing keep the press release ready?"),
                             (T, "Yes, I'll tell Sarah to hold it."), (M, "I'll update the app store screenshots by Friday.")],
         ["June 17|17 June", "payment", "press release", "Sarah", "Friday"]),
    _seg("en-pricing", "en", [(M, "Our competitor dropped their price to 19 dollars a month."),
                              (T, "We stay at 25 but add a free annual plan discount of 20 percent."),
                              (M, "I'll draft the pricing page copy."), (T, "Finance needs the numbers by the 15th.")],
         ["19", "25", "20 percent|20%", "pricing page", "15th"]),
    _seg("en-office", "en", [(T, "The Berlin office lease ends in September."),
                             (M, "Renewing costs 12 percent more; a smaller space nearby is 30 percent cheaper."),
                             (T, "Let's visit the smaller space next Wednesday."), (M, "I'll book the visit with the agent.")],
         ["Berlin", "September", "12", "30", "Wednesday"]),
    _seg("en-data", "en", [(T, "The customer database migration to Postgres is 80 percent done."),
                           (M, "What is blocking the rest?"),
                           (T, "Two legacy reports still query the old Oracle tables."),
                           (M, "I'll rewrite the reports; you keep Oracle read-only until the end of the month.")],
         ["Postgres", "80", "Oracle", "reports", "end of the month|month"]),
    _seg("en-travel", "en", [(M, "The trade show in Tokyo is on October 9."),
                             (T, "Flights are booked for three people; hotel is still open."),
                             (M, "I'll book the hotel near the venue today."), (T, "I'll print 500 brochures.")],
         ["Tokyo", "October 9|9 October", "three|3", "hotel", "500"]),
    _seg("en-support", "en", [(T, "Support tickets went up 40 percent after the last update."),
                              (M, "Most are about the login page timing out."),
                              (T, "I'll roll back the session change tonight."), (M, "I'll write a status page post.")],
         ["40", "login", "roll back|rollback|revert", "status page"]),
    _seg("en-intern", "en", [(M, "Two interns start on July 1."), (T, "They need laptops and accounts before day one."),
                             (M, "IT says laptops take five working days."), (T, "Then I'll order them tomorrow.")],
         ["July 1|1 July", "laptops", "accounts", "five|5", "tomorrow"]),
    _seg("en-audit", "en", [(T, "The external audit starts on the 4th and lasts two weeks."),
                            (M, "We still miss the signed contracts for three vendors."),
                            (T, "Legal can sign them by Monday."), (M, "I'll collect the invoices for Q3.")],
         ["4th", "two weeks|2 weeks", "three|3", "Monday", "Q3"]),
    _seg("ru-budget", "ru", [(T, "Бюджет на рекламу в следующем квартале — два миллиона рублей."),
                             (M, "Половину потратим на онлайн, половину на выставку в Казани."),
                             (T, "Выставка будет двенадцатого мая."), (M, "Я подготовлю смету до пятницы.")],
         ["два миллиона|2 млн|2 000 000|2000000", "Казан", "мая|май", "смет", "пятниц"]),
    _seg("ru-delivery", "ru", [(T, "Поставка серверов задерживается на три недели."),
                               (M, "Тогда запуск переносим на конец июля."),
                               (T, "Я сообщу клиенту сегодня."), (M, "А я обновлю план проекта.")],
         ["три недели|3 недели", "июл", "клиент", "план"]),
    _seg("ru-hiring", "ru", [(M, "Нам нужен ещё один тестировщик."),
                             (T, "Кандидатов пять, двое знают автоматизацию."),
                             (M, "Давай пригласим этих двоих на собеседование в среду."),
                             (T, "Хорошо, я отправлю им приглашения.")],
         ["тестировщ", "пять|5", "двое|два|2", "сред", "приглаш"]),
    _seg("ru-office", "ru", [(T, "Кондиционер в переговорной сломан уже неделю."),
                             (M, "Ремонт стоит сорок тысяч рублей."),
                             (T, "Согласую оплату с бухгалтерией."), (M, "Я вызову мастера на понедельник.")],
         ["кондиционер", "сорок тысяч|40 000|40000|40 тыс", "бухгалтер", "понедельник"]),
    _seg("ru-training", "ru", [(M, "Обучение для новых менеджеров проведём онлайн."),
                               (T, "Нужно четыре занятия по два часа."),
                               (M, "Первое занятие — в следующий вторник."), (T, "Я подготовлю презентацию.")],
         ["онлайн", "четыре|4", "два часа|2 часа", "вторник", "презентац"]),
]

# ~5 minutes of meeting speech (~150 words per minute): the timed topic.
_FIVE_MIN = [(T, "Let's go through the quarterly plan for the support team before the board meeting next Thursday."),
             (M, "Sure. Ticket volume grew from about nine thousand to twelve thousand a month since the spring release."),
             (T, "Most of the growth is password resets and the login page timing out on slow connections."),
             (M, "The engineering team says the timeout fix ships in the next release on the twentieth."),
             (T, "Good. Then we should still plan for the peak in December, when volume usually doubles."),
             (M, "I suggest we hire three temporary agents from November to January, and train them in two weeks."),
             (T, "The budget allows two temporary agents, unless finance approves the extra headcount."),
             (M, "Then I'll write the request to finance with the ticket numbers and the cost per ticket."),
             (T, "Please also include the self-service numbers; the help center deflected about fifteen percent."),
             (M, "Right, and the new chatbot pilot answered four hundred questions in its first week."),
             (T, "Was the satisfaction score for the chatbot acceptable? Last time people complained a lot."),
             (M, "It was three point eight out of five, lower than the four point four for human agents."),
             (T, "So we keep humans for billing questions and let the bot handle resets and shipping status."),
             (M, "Agreed. I'll change the routing rules on Monday and watch the scores for two weeks."),
             (T, "One more item: the Spanish queue has a backlog of three days because Maria is on leave."),
             (M, "Can we move one agent from the English queue? Carlos speaks Spanish."),
             (T, "Yes, move Carlos for the next three weeks. I'll tell his manager today."),
             (M, "And the weekend shift: we still have only one person on Sundays."),
             (T, "Let's rotate the Sunday shift among the six senior agents, one weekend each."),
             (M, "I'll publish the rotation in the team calendar by Friday."),
             (T, "For the board deck, I need three slides: volume, response time and satisfaction."),
             (M, "Average first response time is six hours now; our target is four hours."),
             (T, "Put the target and the plan to get there on the second slide."),
             (M, "Okay. The plan is the timeout fix, the extra agents, and the chatbot routing."),
             (T, "Also mention the knowledge base rewrite that the documentation team starts in October."),
             (M, "They estimated eight weeks for the top fifty articles."),
             (T, "Fine. Send me the draft deck by Tuesday evening so I can review it before Thursday."),
             (M, "Will do. Anything else for today?"),
             (T, "No, that's all. Thanks, and remind me to check the finance answer next week."),
             (M, "Sure, I'll add it to the notes.")] * 2
_FIVE_MIN_RUBRIC = ["Thursday", "twelve thousand|12,000|12000", "three|3", "finance", "Carlos", "Sunday", "Tuesday"]

_NUM = re.compile(r"\d+(?:[.,]\d+)?")
_ZH_NUM = {"零": 0, "一": 1, "二": 2, "兩": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def _zh_digits(text: str) -> str:
    """Chinese numerals of up to 4 digits (三百 -> 300, 十二 -> 12, 一千五百 -> 1500) as Arabic digits."""
    def conv(m: re.Match) -> str:
        total, cur = 0, 0
        for ch in m.group(0):
            if ch in _ZH_NUM:
                cur = _ZH_NUM[ch]
            else:
                unit = {"十": 10, "百": 100, "千": 1000}[ch]
                total += (cur or 1) * unit
                cur = 0
        return str(total + cur)
    return re.sub(r"[零一二兩两三四五六七八九十百千]+", conv, text)


def _numbers(text: str) -> set[str]:
    return {n.replace(",", "") for n in _NUM.findall(_zh_digits(text))}


def invented_numbers(summary: str, transcript: str) -> list[str]:
    """Numbers in the notes that are in neither the transcript nor obviously harmless (list numbering 1-5)."""
    allowed = _numbers(transcript) | {str(i) for i in range(1, 6)}
    return sorted(_numbers(summary) - allowed)


def rubric_score(summary: str, rubric: list[str]) -> float:
    """Fraction of rubric facts present (case- and whitespace-insensitive; `a|b` = any variant counts)."""
    norm = "".join(summary.lower().split())
    hit = sum(any("".join(v.lower().split()) in norm for v in k.split("|")) for k in rubric)
    return hit / len(rubric) if rubric else 0.0


_SCRIPTS = {"zh": re.compile(r"[一-鿿]"), "ru": re.compile(r"[Ѐ-ӿ]"), "en": re.compile(r"[A-Za-z]")}
_LETTER = re.compile(r"[A-Za-zЀ-ӿ一-鿿぀-ヿ]")
_LABELS = re.compile(r"\b(?:TITLE|ACTION|me|them)\b", re.I)


def language_ok(summary: str, lang: str, min_share: float = 0.6) -> bool:
    """The notes are written in the transcript's language: >= 60% of letters in its script (format labels and
    Latin product names like React aside). zh counts Han characters (a zh character counts as one letter)."""
    text = _LABELS.sub("", summary)
    letters = _LETTER.findall(text)
    if not letters:
        return False
    if lang == "zh":  # a Han character carries a word; compare against Latin/Cyrillic words, not letters
        han = len(_SCRIPTS["zh"].findall(text))
        words = len(re.findall(r"[A-Za-zЀ-ӿ]+", text))
        return han / (han + words) >= min_share if han + words else False
    return len(_SCRIPTS[lang].findall(text)) / len(letters) >= min_share


def notes_text(raw: str) -> str:
    """What the app writes for one topic (title, key points, actions), without the heading's timestamps."""
    from tinglongwu.topics import parse_summary

    tp = parse_summary(raw)
    return "\n".join([tp.title, *tp.bullets, *(f"{who}: {what}" for who, what in tp.actions)])


def to_traditional(text: str) -> str:
    """Simplified -> Traditional (OpenCC, a tinglongwu dependency), so 三百万 / 周五 count for 三百萬 / 週五."""
    try:
        from opencc import OpenCC
    except ImportError:
        return text
    return OpenCC("s2t").convert(text)


def score_segment(seg: dict, raw: str) -> dict:
    notes = notes_text(raw)
    transcript = " ".join(t for _, t in seg["lines"])
    facts_text = to_traditional(notes) if seg["lang"] == "zh" else notes
    return {"id": seg["id"], "lang": seg["lang"], "facts": rubric_score(facts_text, seg["rubric"]),
            "invented": invented_numbers(notes, transcript), "lang_ok": language_ok(notes, seg["lang"]),
            "notes": notes}


def quality_summary(scores: list[dict]) -> dict:
    """Per language: key-fact share, segments with an invented number, segments in the right language."""
    out = {}
    for lang in ("zh", "en", "ru"):
        s = [x for x in scores if x["lang"] == lang]
        if s:
            out[lang] = {"n": len(s), "facts": statistics.mean(x["facts"] for x in s),
                         "invented": sum(bool(x["invented"]) for x in s), "lang_ok": sum(x["lang_ok"] for x in s)}
    return out


def quality_key(row: dict) -> tuple:
    q = row["quality"]
    n = sum(v["n"] for v in q.values()) or 1
    facts = sum(v["facts"] * v["n"] for v in q.values()) / n
    invented = sum(v["invented"] for v in q.values()) / n
    lang_ok = sum(v["lang_ok"] for v in q.values()) / n
    return (round(facts - invented, 3), round(lang_ok, 3), -row["peak_ram_mb"])


def quality_ok(row: dict) -> bool:
    """#1378 quality check: notes in the transcript's language (>= 90% of segments) and >= 60% of the key facts
    in every language. A default that answers a Chinese meeting in English is not usable, however fast it is."""
    q = row["quality"]
    return bool(q) and all(v["lang_ok"] >= MIN_LANG_OK * v["n"] and v["facts"] >= MIN_FACTS for v in q.values())


def qualifies(row: dict) -> bool:
    return (row["sec_5min_topic"] < MAX_TOPIC_S and row["peak_ram_mb"] < MAX_RAM_MB
            and row["subtitle_p50_delta_pct"] <= MAX_P50_DELTA_PCT and quality_ok(row))


def pick_model(rows: list[dict]) -> dict | None:
    """Best quality among the models that meet the 60 s / 3 GB / +10% budget; None if none qualifies."""
    ok = [r for r in rows if qualifies(r)]
    return max(ok, key=quality_key) if ok else None


def budget(r: dict) -> str:
    miss = [k for k, bad in (("time", r["sec_5min_topic"] >= MAX_TOPIC_S), ("RAM", r["peak_ram_mb"] >= MAX_RAM_MB),
                             ("subtitles", r["subtitle_p50_delta_pct"] > MAX_P50_DELTA_PCT),
                             ("quality", not quality_ok(r))) if bad]
    return "ok" if not miss else "over: " + ", ".join(miss)


def render_table(runs: dict[str, list[dict]]) -> str:
    out = []
    for machine, rows in runs.items():
        out += [f"### {machine}", "",
                "| model | file MB | peak RAM MB | s / 5-min topic | s / short topic | zh facts | en facts | ru facts | "
                "invented numbers | right language | subtitle p50 delta | budget |",
                "|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for r in rows:
            q = r["quality"]
            n = sum(v["n"] for v in q.values())
            inv = sum(v["invented"] for v in q.values())
            ok = sum(v["lang_ok"] for v in q.values())
            facts = " | ".join(f"{q[k]['facts']:.2f}" if k in q else "-" for k in ("zh", "en", "ru"))
            out.append(f"| {r['model']} | {r.get('file_mb', 0):.0f} | {r['peak_ram_mb']:.0f} | {r['sec_5min_topic']:.1f} | "
                       f"{r['sec_per_topic']:.1f} | {facts} | {inv}/{n} | {ok}/{n} | "
                       f"{r['subtitle_p50_delta_pct']:+.1f}% | {budget(r)} |")
        best = pick_model(rows)
        if best:
            out += ["", f"Chosen ({machine}): **{best['model']}** (best quality within < {MAX_TOPIC_S:.0f} s per "
                        f"5-minute topic, < {MAX_RAM_MB / 1000:.0f} GB RAM, subtitle p50 within +{MAX_P50_DELTA_PCT:.0f}%, "
                        f"right language and key facts)", ""]
        else:
            fb = max(rows, key=quality_key) if rows else None
            out += ["", f"Chosen ({machine}): none meets the budget"
                        + (f"; best quality fallback: **{fb['model']}**" if fb else ""), ""]
    return "\n".join(out)


def probe_once() -> None:
    """One subtitle-like CPU unit of work (stand-in for a short ASR/MT step), roughly 10-30 ms."""
    h = b"tlwu"
    for _ in range(20000):
        h = hashlib.sha256(h).digest()


def measure_probe(stop: threading.Event | None, n: int = 40, gap_s: float = 0.05, work=probe_once) -> float:
    """Median seconds of `work` over up to n runs (stops early when `stop` is set); gap_s sleeps between runs."""
    lat = []
    for _ in range(n):
        if stop is not None and stop.is_set():
            break
        t = time.perf_counter()
        work()
        lat.append(time.perf_counter() - t)
        time.sleep(gap_s)
    return statistics.median(lat) if lat else 0.0


def _busy(stop) -> None:  # one core of ASR/MT-like load at normal priority
    h = b"x"
    while not stop.is_set():
        for _ in range(2000):
            h = hashlib.sha256(h).digest()


class Busy:
    """n processes that each keep one core busy (the live ASR + MT the summarizer must not slow down)."""

    def __init__(self, n: int):
        self.n, self.stop, self.procs = n, multiprocessing.Event(), []

    def __enter__(self):
        self.procs = [multiprocessing.Process(target=_busy, args=(self.stop,), daemon=True) for _ in range(self.n)]
        for p in self.procs:
            p.start()
        return self

    def __exit__(self, *exc):
        self.stop.set()
        for p in self.procs:
            p.join(5)


def rss_mb() -> float:
    import psutil
    return psutil.Process().memory_info().rss / 1e6


def peak_rss_mb() -> float:
    """Peak RSS of this process so far (MB)."""
    try:
        import resource
        kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return kb * 1024 / 1e6 if sys.platform != "darwin" else kb / 1e6  # Linux: KiB, macOS: bytes
    except ImportError:  # Windows
        import psutil
        return psutil.Process().memory_info().peak_wset / 1e6


def bench_model(name: str, summarize, base_rss_mb: float, *, busy: int = 0, probe=measure_probe,
                segments=SEGMENTS, five_min=_FIVE_MIN, peak=peak_rss_mb) -> dict:
    """summarize(prompt) -> raw notes (the app's prompt). Scores every segment, times the 5-minute topic under
    `busy` cores of load, and measures the subtitle probe p50 with and without the summarizer running."""
    from tinglongwu.topics import Line, build_prompt

    def prompt(lines):
        return build_prompt([Line(float(i), side, text) for i, (side, text) in enumerate(lines)])

    scores, secs = [], []
    for seg in segments:
        t = time.perf_counter()
        raw = summarize(prompt(seg["lines"]))
        secs.append(time.perf_counter() - t)
        scores.append(score_segment(seg, raw))
    done, timed = threading.Event(), {}

    def worker():
        try:
            t = time.perf_counter()
            timed["raw"] = summarize(prompt(five_min))
            timed["s"] = time.perf_counter() - t
        except BaseException as e:  # re-raised below: a failed 5-minute topic is not a 0-second measurement
            timed["err"] = e
        finally:
            done.set()

    with Busy(busy):  # the probe baseline runs under the same ASR/MT load, so the delta is the summarizer's alone
        base = probe(None)
        th = threading.Thread(target=worker, daemon=True)
        th.start()
        loaded = probe(done)
        th.join()
    if "err" in timed:
        raise RuntimeError(f"{name}: 5-minute topic failed") from timed["err"]
    long_notes = timed.get("raw", "")
    return {"model": name, "peak_ram_mb": max(0.0, peak() - base_rss_mb),
            "sec_per_topic": statistics.mean(secs) if secs else 0.0, "sec_5min_topic": timed.get("s", 0.0),
            "five_min_facts": rubric_score(long_notes, _FIVE_MIN_RUBRIC), "busy_cores": busy,
            "quality": quality_summary(scores), "segments": scores, "five_min_notes": long_notes,
            "subtitle_p50_base_s": base, "subtitle_p50_loaded_s": loaded,
            "subtitle_p50_delta_pct": pct_delta(base, loaded)}


def pct_delta(base: float, under_load: float) -> float:
    return (under_load - base) / base * 100.0 if base > 0 else 0.0


def run_llama(name: str, gguf: str, threads: int | None, busy: int) -> dict:
    from tinglongwu.topics import llama_summarizer, lower_thread_priority

    base = rss_mb()
    raw = llama_summarizer(gguf, threads=threads)  # the app's loader: CPU, n_ctx 4096, cpus // 4 threads, greedy

    def summarize(prompt: str) -> str:
        lower_thread_priority()  # the app's worker thread runs at the lowest priority
        return raw(prompt)

    summarize("Summarize: [me] hello. [them] hi.")  # load + warm-up, excluded
    row = bench_model(name, summarize, base, busy=busy)
    row["file_mb"] = Path(gguf).stat().st_size / 1e6
    row["sha256"] = _sha256(Path(gguf))
    return row


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def fetch(names: list[str], dest: Path, hub=None) -> dict[str, str | None]:
    """Download each candidate's GGUF into dest/<name>/ (bench only; runs before tinglongwu's network guard)."""
    sys.path.insert(0, str(ROOT / "scripts" / "bench"))
    from mt_cpu_threads import pick_quants

    if hub is None:
        import huggingface_hub as hub
    got = {}
    for n in names:
        c = CANDIDATES[n]
        info = hub.model_info(c["repo"])
        f = pick_quants([s.rfilename for s in info.siblings], [c["quant"]])[c["quant"]]
        got[n] = hub.hf_hub_download(c["repo"], f, revision=info.sha, local_dir=str(dest / n)) if f else None
        (dest / n).mkdir(parents=True, exist_ok=True)
        (dest / n / "source.json").write_text(json.dumps({"repo": c["repo"], "revision": info.sha, "file": f}), "utf-8")
        print(f"topic_bakeoff: {n}: {c['repo']}@{info.sha} {f or 'no ' + c['quant']}", file=sys.stderr, flush=True)
    return got


def parse_names(text: str | None) -> list[str]:
    names = [x.strip() for x in (text or "").split(",") if x.strip()] or list(CANDIDATES)
    bad = [n for n in names if n not in CANDIDATES]
    if bad:
        raise ValueError(f"unknown candidate(s): {', '.join(bad)} (known: {', '.join(CANDIDATES)})")
    return names


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--model", action="append", default=[], metavar="NAME=GGUF", help="repeat for each model (Q4_K_M GGUF)")
    ap.add_argument("--threads", type=int, default=None, help="summarizer threads (default: the app's cpus // 4)")
    ap.add_argument("--busy", type=int, default=2, help="cores kept busy while the 5-minute topic is timed (ASR + MT)")
    ap.add_argument("--machine", default=None, help="label for the table, e.g. '4 vCPU runner'")
    ap.add_argument("--out", default=None, help="write this run's rows as JSON")
    ap.add_argument("--results", nargs="+", metavar="JSON", help="skip running; merge saved --out files into the table")
    ap.add_argument("--fetch", default=None, metavar="NAMES", help="download candidate GGUFs (comma list, '' = all)")
    ap.add_argument("--dir", default="cand", help="--fetch destination")
    a = ap.parse_args(argv)
    if a.fetch is not None:
        got = fetch(parse_names(a.fetch), Path(a.dir))
        return 0 if all(got.values()) else 1
    if a.results:
        runs: dict[str, list[dict]] = {}
        for p in a.results:
            d = json.loads(Path(p).read_text("utf-8"))
            runs.setdefault(d["machine"], []).extend(d["rows"])
        print(render_table(runs))
        return 0
    if not a.model:
        ap.error("give at least one --model NAME=GGUF (or --results / --fetch)")
    rows = []
    for spec in a.model:
        name, _, path = spec.partition("=")
        if not path or not Path(path).expanduser().is_file():
            ap.error(f"--model {spec!r}: expected NAME=path-to.gguf")
        print(f"[{name}] running ...", flush=True)
        rows.append(run_llama(name, str(Path(path).expanduser()), a.threads, a.busy))
        for sc in rows[-1]["segments"]:  # the notes themselves go to the log, for the manual quality read
            print(f"--- {name} {sc['id']} facts={sc['facts']:.2f} invented={sc['invented']} lang_ok={sc['lang_ok']}\n"
                  f"{sc['notes']}", file=sys.stderr)
        print(f"--- {name} 5-minute topic\n{rows[-1]['five_min_notes']}", file=sys.stderr, flush=True)
        print(f"--- {name} sha256 {rows[-1]['sha256']} size {Path(path).stat().st_size}", flush=True)
    machine = a.machine or f"{os.cpu_count()} vCPU"
    if a.out:
        Path(a.out).write_text(json.dumps({"machine": machine, "rows": rows}, indent=1, ensure_ascii=False), "utf-8")
    print(render_table({machine: rows}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
