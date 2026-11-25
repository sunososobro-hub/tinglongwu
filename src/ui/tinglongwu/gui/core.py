"""GUI logic without any tkinter: event queue sink, subtitle state, settings persistence.

Worker threads only ever put tuples on a queue.Queue; the tkinter main thread polls it with after().
Events: ("draft", src_text, translation) | ("final", src_text, target_text) | ("clear",) |
        ("partial", src_text, translation_so_far) (#1771: fills the pending row until its "final") |
        ("amend", replaced_src_text, src_text, target_text) (#1395: replaces that final line) |
        ("status", text) | ("notice", text) | ("error", text) | ("stopped",)
"""
from __future__ import annotations

import re

from .. import diag as _diag

import json
import os
import queue
import sys
import unicodedata
from pathlib import Path

from ..sink import Result, Sink
from ..langs import SOURCE_LANGS, SameLanguageError, lang_base  # noqa: F401 - #1664: moved down, kept here
from ..settings import (  # noqa: F401 - #1664: gui.json settings live in tinglongwu.settings; same names here
    ALPHA_MAX, ALPHA_MIN, ALPHA_STEP, DEFAULTS, ENGINES, FONT_MAX, FONT_MIN, QUALITIES, ROWS_MAX, ROWS_MIN,
    SEND_MODES, _gpu_usable, clamp_alpha, clamp_font, clamp_rows, load_settings, save_settings, settings_path,
    target_for_locale,
)
from ..stream import DraftRenderer

GUI_SEND_MODES = ("auto", "confirm")  # #1052: what Settings offers; the 2 s countdown was too short to cancel


def send_gate_line(view) -> str:
    """#833: the held speak line as one status line ("" when nothing is held); view = reverse.sendgate.GateView."""
    from ..i18n import t

    if view.state == "counting":
        return t("gui_send_hold", s=f"{view.remaining:.0f}" if view.remaining >= 1 else f"{view.remaining:.1f}",
                 text=view.text)
    if view.state == "asking":  # #968: the reason is shown, so a held line never waits without saying why
        return t("gui_send_ask", text=f"[{view.reason}] {view.text}" if view.reason else view.text)
    if view.state == "rejected":
        return t("gui_send_unclear", reason=view.reason, text=view.text)
    return ""


def send_mode_notice_key(mode: str) -> str | None:
    """#834: i18n key of the one-line quality notice shown when a direct-send mode is chosen, else None."""
    return "gui_send_mode_notice" if mode in ("countdown", "auto") else None
# #983: settings the running pipeline reads only when it starts; changing one during a run needs a restart
START_TIME_SETTINGS = ("lang", "target", "engine", "quality", "model", "preset", "glossary", "source", "voice",
                       "voice_device", "send_mode", "listen_device", "mic_device", "gpu_use")


def restart_needed(at_start: dict, now: dict) -> list[str]:
    """#983: the start-time settings whose value in `now` differs from what the running pipeline started with."""
    return [k for k in START_TIME_SETTINGS if k in at_start and k in now and at_start[k] != now[k]]


LANGS = SOURCE_LANGS  # auto, focus ja/en/ru, zh, then the experimental ones (#58)
TARGETS = ["zh-tw", "zh-cn", "en", "ja", "ko", "es", "fr", "de", "th", "vi", "id"]  # #1163: Hy-MT2 direct targets
PRESETS = ["meeting", "chat"]  # meeting = whole sentences (~5-8 s latency); chat = lower latency, more fragments
SOURCES = ["system", "mic"]  # system = whatever the PC is playing (loopback); mic = microphone
MODELS = ["tiny", "base", "small", "medium", "large-v3", "large-v3-turbo"]
FONT_PRESETS = {"S": 20, "M": 28, "L": 36, "XL": 48}  # subtitle size picker in the control window
HISTORY_LINES = 200  # finals kept in the subtitle bar's scroll-back


QUICK_MODES = ("listen", "speak")  # #294 first-screen buttons: "Listen to others" / "I speak"
SESSION_MODES = ("listen", "speak", "two-way")  # #981: two-way = both legs (meeting / face-to-face)


def session_route(mode: str, mine: str | None, other: str | None) -> list[dict]:
    """#981: the only place that turns (mode, My language, Other side language) into what a session runs.
    Returns one leg per direction, each {"leg", "lang" (source), "target", "source" (capture)}:
    listen = other -> mine, captured from the PC sound (loopback); speak = mine -> other, captured from the
    microphone (the system default one), whatever capture source was saved; two-way = both legs.
    mine = a TARGETS value ("zh" = zh-tw; anything else = the default); other = a SOURCE_LANGS value ("auto" when
    unknown). other == "auto": listen detects the speaker, speak keeps the #294 fallback (English for a Chinese
    speaker, Traditional Chinese otherwise). Raises SameLanguageError when both are the same language (zh-tw vs zh
    counts as the same), so a leg never has source == target."""
    if mode not in SESSION_MODES:
        raise ValueError(f"unknown session mode: {mode!r}")
    mine = "zh-tw" if mine == "zh" else (mine if mine in TARGETS else DEFAULTS["target"])
    other = other if other in SOURCE_LANGS else "auto"
    if other != "auto" and lang_base(other) == lang_base(mine):
        raise SameLanguageError(f"My language and Other side language are both {lang_base(mine)!r}")
    legs = []
    if mode in ("listen", "two-way"):
        legs.append({"leg": "listen", "lang": other, "target": mine, "source": "system"})
    if mode in ("speak", "two-way"):
        if other == "auto":
            to = "en" if lang_base(mine) == "zh" else "zh-tw"
        else:
            to = "zh-tw" if other == "zh" else other
        legs.append({"leg": "speak", "lang": lang_base(mine), "target": to, "source": "mic"})
    return legs


def quick_settings(s: dict, mode: str) -> dict:
    """#294 easy start: session settings for a first-screen button; `s` (the saved settings) is not changed.
    My language = `target`; the other side's language = `lang` (#737, auto by default). Source, target and capture
    come only from session_route() (#981): listen = PC sound, other -> mine; speak = microphone, mine -> other.
    Raises SameLanguageError when both languages are the same. Engine/model stay on `quality`."""
    if mode not in QUICK_MODES:
        raise ValueError(f"unknown quick mode: {mode!r}")
    (leg,) = session_route(mode, s.get("target"), s.get("lang"))
    out = dict(s)
    out.update({k: v for k, v in leg.items() if k != "leg"})
    return out


SPEAK_HINT_CABLE = "gui_ready_speak_cable"  # #710 ready line: "set the Meet/Zoom microphone to CABLE Output"
SPEAK_HINT_NO_CABLE = "gui_speak_no_cable"  # #710 one line + vb-audio.com/Cable link
SPEAK_PANEL_HINTS = (SPEAK_HINT_CABLE, SPEAK_HINT_NO_CABLE)


def meeting_cable(names=None, platform: str | None = None) -> str | None:
    """#710: the VB-CABLE playback device ("CABLE Input") the Speak button talks into on Windows, or None (not
    installed, or not Windows: macOS BlackHole routing is #687). names=None lists soundcard's output devices."""
    if (platform or sys.platform) != "win32":
        return None
    if names is None:
        try:
            import soundcard as sc

            names = [sp.name for sp in sc.all_speakers()]
        except Exception:  # noqa: BLE001 - no audio stack: treated as "no cable"
            _diag.swallowed(__name__)
            names = []
    from ..reverse.vmic import pick_win_cable

    return pick_win_cable(names)


def speak_route(run: dict, cable: str | None, platform: str | None = None) -> str | None:
    """#710: one press of Speak = my speech -> translation -> voice into the meeting. With VB-CABLE the session's
    translated voice is played on `cable` (Meet/Zoom microphone = "CABLE Output"); `run` is changed in place and
    the ready-status i18n key is returned. Windows without VB-CABLE: `run` stays subtitles-only and the key of a
    one-line notice (with the vb-audio.com link) is returned; a voice saved for Listen is switched off for this run,
    so nothing is played on the old earphones while the notice says "subtitles only". Other systems: None, `run`
    unchanged."""
    if cable:
        run.update(voice=True, voice_device=cable, speak_cable=True)
        return SPEAK_HINT_CABLE
    if (platform or sys.platform) == "win32":
        run.update(voice=False, speak_cable=False)
        return SPEAK_HINT_NO_CABLE
    return None


def speak_start_view(hint: str | None) -> tuple[str, bool]:
    """#710 (review of #749): what App.toggle() shows when a Speak session starts: (notice i18n key or "",
    open the Adjust panel). Both Speak lines live in the Adjust panel, which stays withdrawn when Speak is pressed on
    the bar, so the panel is opened for them; Listen / other systems (hint None) leave it as it is."""
    return (hint if hint == SPEAK_HINT_NO_CABLE else ""), hint in SPEAK_PANEL_HINTS


def quick_buttons(phase: str, active: str | None = None) -> dict[str, tuple[str, bool]]:
    """#294: {mode: (i18n key, enabled)} for the first-screen buttons. phase: idle | loading (models not ready:
    both read "Loading...", nothing to press) | running (the pressed one is Stop, the other is off) | busy
    (stopping / downloading: nothing to press)."""
    if phase == "loading":
        return {m: ("gui_loading", False) for m in QUICK_MODES}
    if phase == "running":
        return {m: ("gui_stop", True) if m == active else (f"gui_quick_{m}", False) for m in QUICK_MODES}
    return {m: (f"gui_quick_{m}", phase == "idle") for m in QUICK_MODES}


BOTH_MODE = "both"  # #1001: the one first-screen Start: listen (PC sound -> mine) and speak (mic -> other) together
SPEAK_MARK = "🎤 "  # #1001: a line of the speak leg in the shared subtitle list starts with this (my words)


def both_settings(s: dict) -> tuple[dict, dict]:
    """#1001: (listen run, speak run) for one Start; `s` is not changed. Same legs as session_route("two-way").
    The speak leg shows finished lines only (no live draft, no "heard" placeholder: the listen leg owns the draft
    row, two drafts would overwrite each other) and never saves (both legs would write the same file).
    Raises SameLanguageError when both languages are the same."""
    session_route("two-way", s.get("target"), s.get("lang"))  # raises before any run is built
    listen, speak = quick_settings(s, "listen"), quick_settings(s, "speak")
    speak.update(live_draft=False, save=False, mine=s.get("target"))  # mine: script of my own words (#1070)
    return listen, speak


WATCH_MODE = "watch"  # 0.3.8: scene "video": listen only (floating subtitles); no mic, no speech, nothing to CABLE
SCENES = ("meeting", "video")


def scene_of(s: dict) -> str:
    """The saved scene, "video" (listen only) when missing or unknown."""
    return s.get("scene") if s.get("scene") in SCENES else "video"


def scene_mode(s: dict) -> str:
    """Which session the one Start runs: meeting = BOTH_MODE (listen + speak), video = WATCH_MODE (listen only)."""
    return WATCH_MODE if scene_of(s) == "video" else BOTH_MODE


def watch_settings(s: dict) -> dict:
    """0.3.8 video scene: the listen leg only. The voice output (#330) is forced off too, so nothing is spoken or
    sent to any output; the mic leg is simply not built. `s` is not changed.
    #894: here the saved Source is honoured: "mic" listens to the room (lecture, in-person meeting) through the
    microphone instead of the PC sound. The meeting scene keeps PC sound for listening (its mic is the speak leg)."""
    run = quick_settings(s, "listen")
    run["voice"] = False
    if s.get("source") in SOURCES:
        run["source"] = s["source"]
    return run


def source_label_key(s: dict) -> str | None:
    """#1544: i18n key of the plain-words source line shown next to 情境 in the video scene (None in the meeting scene,
    whose listen leg is always the PC sound). The saved Source can be "mic" (#894), so it is never hidden in 調整."""
    if scene_of(s) != "video":
        return None
    return "gui_src_mic" if s.get("source") == "mic" else "gui_src_system"


def mic_toggle_visible(s: dict) -> bool:
    """#1604: the "my microphone on / off" toggle only means something when I speak too (the meeting scene, the speak leg's
    capture). In the listen-only video scene the situation alone decides the capture source, so the toggle is hidden."""
    return scene_of(s) == "meeting"


def toggled_source(s: dict) -> str:
    """#1544: the Source the one-click toggle switches to (system sound <-> microphone). `s` is not changed."""
    return "system" if s.get("source") == "mic" else "mic"


def scene_runs(s: dict) -> tuple[str, dict, dict | None]:
    """(mode, listen run, speak run or None) for the saved scene; raises SameLanguageError like both_settings."""
    if scene_mode(s) == WATCH_MODE:
        return WATCH_MODE, watch_settings(s), None
    listen, speak = both_settings(s)
    return BOTH_MODE, listen, speak


def start_button(phase: str, active: str | None = None) -> tuple[str, bool]:
    """#1001: (i18n key, enabled) of the one first-screen Start, same phases as quick_buttons()."""
    if phase == "loading":
        return "gui_loading", False
    if phase == "running":
        return ("gui_stop", True) if active in (BOTH_MODE, WATCH_MODE) else ("gui_quick_both", False)
    return "gui_quick_both", phase == "idle"


# #314 first-launch overlay: 3 numbered steps over the real controls. zh-CN has its own text here although the
# rest of the UI still shows zh-TW for a zh-CN system (see tour_lang).
TOUR_TEXT = {
    "en": {"title": "Start in 3 steps", "step1": "1. Pick the language the other side speaks, or Auto:",
           "step2": "2. Press Start (on the bar above).",
           "step3": "3. Subtitles appear here - drag to move", "ok": "Got it"},
    "zh-TW": {"title": "三步開始", "step1": "1. 選對方說的語言，或「自動」：", "step2": "2. 按上方小條上的「開始」。",
              "step3": "3. 字幕會出現在這裡 - 可拖曳移動", "ok": "知道了"},
    "zh-CN": {"title": "三步开始", "step1": "1. 选对方说的语言，或“自动”：", "step2": "2. 按上方小条上的“开始”。",
              "step3": "3. 字幕会出现在这里 - 可拖动移动", "ok": "知道了"},
    "ru": {"title": "Начать за 3 шага", "step1": "1. Выберите язык собеседника или «авто»:",
           "step2": "2. Нажмите «Старт» на панели сверху.",
           "step3": "3. Субтитры появятся здесь - перетащите, чтобы сдвинуть", "ok": "Понятно"},
    "ja": {"title": "3 ステップで開始", "step1": "1. 相手が話す言語、または「自動」を選ぶ:",
           "step2": "2. 上のバーの「スタート」を押す。",
           "step3": "3. 字幕はここに出ます - ドラッグで移動", "ok": "わかった"},
}


def tour_lang(ui: str, system_tag: str | None = None) -> str:
    """UI language of the #314 overlay. Chinese UI ("zh-TW") on a Simplified-Chinese system -> "zh-CN"."""
    if ui == "zh-TW" and target_for_locale(system_tag) == "zh-cn":
        return "zh-CN"
    return ui if ui in TOUR_TEXT else "en"


def tour_text(ui: str, system_tag: str | None = None) -> dict[str, str]:
    return TOUR_TEXT[tour_lang(ui, system_tag)]


def tour_on_start(s: dict) -> bool:
    """#314: the overlay opens by itself only until the user closed it once with "Got it" (kept in gui.json)."""
    return not s.get("tour_seen")


USAGE_CHOICES = ("videos", "meetings", "calls", "learning", "other")  # #680


def usage_should_ask(s: dict) -> bool:
    """#680: ask once, after the first successful subtitle; never again once answered or skipped."""
    return not s.get("usage_asked")


def usage_answer(s: dict, choice: str | None) -> None:
    """#680: store the answer in the local settings dict. Nothing is sent. choice None = skip / closed without
    saving: records that we asked and keeps any answer saved earlier; an unknown string clears the answer."""
    s["usage_asked"] = True
    if choice is None:
        s.setdefault("usage_use", "")
        return
    s["usage_use"] = choice if choice in USAGE_CHOICES else ""


def usage_prefill(s: dict, include: bool) -> dict:
    """#680: extra lines for the 'wrong translation' report prefill (#677). Only when the user left the checkbox
    ticked (`include`) and there is an answer; the language pair is added with it. Empty dict otherwise."""
    use = s.get("usage_use")
    if not include or use not in USAGE_CHOICES:
        return {}
    return {"Mainly used for": use, "Language pair": f"{s.get('lang')} -> {s.get('target')}"}


def report_info(s: dict) -> dict:
    """Current GUI settings for 'Report a problem' (#91): choices only - no paths, no glossary, no subtitle text."""
    return {"Mode": f"gui/{s.get('preset')}", "Language pair": f"{s.get('lang')} -> {s.get('target')}",
            "Quality": s.get("quality"), "Engine": s.get("engine"), "Model": s.get("model"), "Source": s.get("source"),
            "Live draft": "on" if s.get("live_draft") else "off", "Voice": "on" if s.get("voice") else "off"}


def doctor_lang(source_lang: str | None) -> str:
    """Voice language for 'Check setup' (#313): the language the other side speaks and hears; ru when unknown/auto."""
    from ..doctor import VOICE_LANGS

    return source_lang if source_lang in VOICE_LANGS else "ru"


def setup_check(lang: str, run=None) -> tuple[str, bool]:
    """'Check setup' button (#313 part 2): the `tlwu doctor` lines (one per check, with its fix) + summary, and
    whether nothing FAILed. `run` is doctor.run_checks (injectable for tests)."""
    from ..doctor import FAIL, run_checks, summary

    checks = (run or run_checks)(lang)
    return "\n".join([c.line() for c in checks] + [summary(checks)]), not any(c.status == FAIL for c in checks)


def run_setup_check(q: queue.Queue, lang: str, run=None) -> None:
    """Worker-thread body: probes may be slow (nvidia-smi, device listing), so the result goes on the GUI queue
    as ("doctor", text, ok); a crash in a probe is reported, never raised into the thread."""
    try:
        text, ok = setup_check(lang, run)
    except Exception as e:  # noqa: BLE001
        text, ok = f"check failed: {type(e).__name__}: {e}", False
    q.put(("doctor", text, ok))


def font_preset(size) -> str:
    """Nearest S/M/L/XL label for a font size (what the size picker shows for a custom Ctrl+wheel size)."""
    n = clamp_font(size)
    return min(FONT_PRESETS, key=lambda k: (abs(FONT_PRESETS[k] - n), FONT_PRESETS[k]))


def rows_after_drag(start_rows: int, dy_px: float, line_px: float) -> int:
    """Subtitle bar height while its top edge is dragged: up (dy < 0) adds rows, down removes them."""
    if line_px <= 0:
        return clamp_rows(start_rows)
    return clamp_rows(start_rows + round(-dy_px / line_px))


def follow_newest(yview_last: float) -> bool:
    """Auto-scroll to the newest line only while the view already shows the bottom (the user has not
    scrolled back to read something)."""
    return yview_last >= 0.999


_MARK_OK = "\u2713 "
_MARK_MISSING_OLD = " (\u9700\u4e0b\u8f09 / not downloaded)"  # #739: no longer shown; kept so an old saved label still parses
_MARK_HEAVY = " (too heavy for this PC)"  # #780: medium / large-v3 on a CPU-mode PC with < 24 GB RAM
_MARK_MISSING = {"en": " (not downloaded)", "ru": " (нужна загрузка)", "zh-TW": "（需下載）", "ja": "（要ダウンロード）"}  # one language per label (#884)

# #884: the dropdowns show names in the UI language; the stored setting keeps the raw value.
_CHOICES = {
    "source": {"en": {"system": "PC sound", "mic": "Microphone"},
               "ru": {"system": "Звук ПК", "mic": "Микрофон"},
               "zh-TW": {"system": "電腦音效", "mic": "麥克風"},
               "ja": {"system": "PC の音", "mic": "マイク"}},
    "preset": {"en": {"meeting": "Meeting (full sentences)", "chat": "Chat (faster)"},
               "ru": {"meeting": "Встреча (целые фразы)", "chat": "Чат (быстрее)"},
               "zh-TW": {"meeting": "會議（完整句子）", "chat": "聊天（較快）"},
               "ja": {"meeting": "会議（文単位）", "chat": "チャット（速め）"}},
    "gpu_use": {"en": {"auto": "Auto", "off": "Off"}, "ru": {"auto": "Авто", "off": "Выкл."},
                "zh-TW": {"auto": "自動", "off": "關閉"}, "ja": {"auto": "自動", "off": "オフ"}},  # #1531
    "engine": {"en": {"auto": "Auto"}, "ru": {"auto": "Авто"}, "zh-TW": {"auto": "自動"}, "ja": {"auto": "自動"}},
}



# #1806: one key set, a `plain` and a `tech` text per key and UI language. Plain is the default; the "Show technical
# details" setting (or --tech-labels) switches every label() to the technical text. Add a key here (all 4 languages,
# both variants: tests/test_labels_1806.py) instead of a second table, so the two views never drift.
LABEL_VARIANTS = ("plain", "tech")
LABELS = {
    "quality.auto": {
        "en": {"plain": "Auto", "tech": "Auto"}, "ru": {"plain": "Авто", "tech": "Авто"},
        "zh-TW": {"plain": "自動", "tech": "自動"}, "ja": {"plain": "おまかせ", "tech": "自動"}},
    "quality.fast": {
        "en": {"plain": "Faster", "tech": "Fast"}, "ru": {"plain": "Быстрее", "tech": "Быстро"},
        "zh-TW": {"plain": "快一點", "tech": "快速"}, "ja": {"plain": "速さ重視", "tech": "高速"}},
    "quality.balanced": {
        "en": {"plain": "Just right", "tech": "Balanced"}, "ru": {"plain": "В самый раз", "tech": "Баланс"},
        "zh-TW": {"plain": "剛剛好", "tech": "平衡"}, "ja": {"plain": "ちょうどよい", "tech": "バランス"}},
    "quality.quality": {
        "en": {"plain": "Most accurate, slower", "tech": "Best quality"},
        "ru": {"plain": "Точнее, но медленнее", "tech": "Лучшее качество"},
        "zh-TW": {"plain": "最準但較慢", "tech": "最佳品質"}, "ja": {"plain": "いちばん正確（やや遅い）", "tech": "最高品質"}},
    "quality.manual": {
        "en": {"plain": "I'll choose", "tech": "Manual"}, "ru": {"plain": "Выберу сам", "tech": "Вручную"},
        "zh-TW": {"plain": "我自己選", "tech": "手動"}, "ja": {"plain": "自分で選ぶ", "tech": "手動"}},
    "ui.gui_model": {
        "en": {"plain": 'Listening accuracy (faster / smarter but slower)', "tech": 'Speech model'},
        "ru": {"plain": 'Точность распознавания', "tech": 'Модель речи'},
        "zh-TW": {"plain": '聽的準確度（快一點／聰明但較慢）', "tech": '語音模型'},
        "ja": {"plain": '聞き取りの精度（速い／賢いが遅め）', "tech": '音声モデル'}},
    "ui.gui_quality": {
        "en": {"plain": 'Speed and accuracy', "tech": 'Quality'},
        "ru": {"plain": 'Скорость и точность', "tech": 'Качество'},
        "zh-TW": {"plain": '速度與準確度', "tech": '品質'},
        "ja": {"plain": '速さと正確さ', "tech": '品質'}},
    "ui.gui_engine": {
        "en": {"plain": 'How to translate', "tech": 'Engine'},
        "ru": {"plain": 'Способ перевода', "tech": 'Движок'},
        "zh-TW": {"plain": '翻譯方式', "tech": '引擎'},
        "ja": {"plain": '翻訳の方法', "tech": 'エンジン'}},
    "ui.gui_gpu_use": {
        "en": {"plain": 'Speed up with the graphics chip (Auto / Off)', "tech": 'Use GPU'},
        "ru": {"plain": 'Ускорение видеокартой', "tech": 'Использовать GPU'},
        "zh-TW": {"plain": '用顯示晶片加速（自動／不要）', "tech": '使用 GPU'},
        "ja": {"plain": 'グラフィックチップで高速化（おまかせ／しない）', "tech": 'GPU を使う'}},
    "ui.gui_mt_path_gpu": {
        "en": {"plain": 'Translation is using the graphics chip to go faster', "tech": 'Hy-MT2 on GPU ({api})'},
        "ru": {"plain": 'Перевод ускоряется видеокартой', "tech": 'Hy-MT2 на GPU ({api})'},
        "zh-TW": {"plain": '翻譯正在用顯示晶片加速', "tech": 'Hy-MT2 在顯示卡上跑（{api}）'},
        "ja": {"plain": '翻訳はグラフィックチップで高速化中', "tech": 'Hy-MT2 は GPU（{api}）'}},
    "ui.gui_mode_gpu": {
        "en": {"plain": 'Speeding up', "tech": 'GPU mode'},
        "ru": {"plain": 'Ускорение включено', "tech": 'Режим GPU'},
        "zh-TW": {"plain": '加速中', "tech": 'GPU 模式'},
        "ja": {"plain": '高速化中', "tech": 'GPU モード'}},
    "ui.gui_mode_cpu": {
        "en": {"plain": 'Normal speed', "tech": 'CPU mode'},
        "ru": {"plain": 'Обычная скорость', "tech": 'Режим CPU'},
        "zh-TW": {"plain": '一般速度', "tech": 'CPU 模式'},
        "ja": {"plain": '通常の速さ', "tech": 'CPU モード'}},
    "ui.gui_cpu_mode": {
        "en": {"plain": 'Normal speed: set up to keep up with the talk', "tech": 'CPU mode (no NVIDIA GPU): settings chosen for speed'},
        "ru": {"plain": 'Обычная скорость: настроено так, чтобы успевать за речью', "tech": 'Режим CPU (нет NVIDIA GPU): настройки для скорости'},
        "zh-TW": {"plain": '一般速度：已自動選擇跟得上說話的設定', "tech": 'CPU 模式（沒有 NVIDIA 顯示卡）：已自動選擇偏重速度的設定'},
        "ja": {"plain": '通常の速さ: 話に遅れないよう自動で設定', "tech": 'CPU モード（NVIDIA GPU なし）: 速度重視の設定'}},
    "ui.gui_cpu_mode_gpu_unused": {
        "en": {"plain": 'Normal speed: the graphics chip is not used; set up to keep up with the talk', "tech": 'CPU mode (NVIDIA GPU found but not used): settings chosen for speed'},
        "ru": {"plain": 'Обычная скорость: видеокарта не используется; настроено так, чтобы успевать за речью', "tech": 'Режим CPU (видеокарта NVIDIA найдена, но не используется): настройки для скорости'},
        "zh-TW": {"plain": '一般速度：沒有用到顯示晶片，已自動選擇跟得上說話的設定', "tech": 'CPU 模式（有 NVIDIA 顯示卡但未使用）：已自動選擇偏重速度的設定'},
        "ja": {"plain": '通常の速さ: グラフィックチップは未使用。話に遅れないよう自動で設定', "tech": 'CPU モード（NVIDIA GPU はあるが未使用）: 速度重視の設定'}},
    "ui.gpu_offer_banner": {
        'en': {"plain": 'Your computer can go faster. Download the speed-up add-on ({size}, one time)?', "tech": 'NVIDIA GPU found — install speed-up pack ({size}, once)?'},
        'ru': {"plain": 'Ваш компьютер может работать быстрее. Скачать ускоряющее дополнение ({size}, один раз)?', "tech": 'Найдена видеокарта NVIDIA — установить пакет ускорения ({size}, один раз)?'},
        'zh-TW': {"plain": '你的電腦可以更快 — 要下載加速套件嗎（約 {size}，只要一次）？', "tech": '偵測到 NVIDIA 顯示卡 — 要安裝 GPU 加速包嗎（{size}，只需一次）？'},
        'ja': {"plain": 'このパソコンはもっと速くできます。高速化アドオンをダウンロードしますか（約 {size}、1 回のみ）？', "tech": 'NVIDIA GPU が見つかりました — 高速化パックをインストールしますか（{size}、1 回のみ）？'}},
    "ui.vk_offer_banner": {
        'en': {"plain": 'Your computer can go faster. Download the speed-up add-on in one click (one time)?', "tech": 'GPU found (Vulkan) — install the GPU speed-up pack and speech model (one click, once)?'},
        'ru': {"plain": 'Ваш компьютер может работать быстрее. Скачать ускоряющее дополнение в один клик (один раз)?', "tech": 'Найдена видеокарта (Vulkan) — установить пакет ускорения GPU и модель речи (один клик, один раз)?'},
        'zh-TW': {"plain": '你的電腦可以更快 — 要一鍵下載加速套件嗎（只要一次）？', "tech": '偵測到 GPU（Vulkan）— 要安裝 GPU 加速包與語音模型嗎（一鍵、只需一次）？'},
        'ja': {"plain": 'このパソコンはもっと速くできます。高速化アドオンをワンクリックでダウンロードしますか（1 回のみ）？', "tech": 'GPU（Vulkan）が見つかりました — GPU 高速化パックと音声モデルをインストールしますか（ワンクリック、1 回のみ）？'}},
    "ui.vk_offer_running": {
        'en': {"plain": 'Downloading the speed-up add-on ...', "tech": 'Installing Vulkan GPU pack and speech model ...'},
        'ru': {"plain": 'Скачиваю ускоряющее дополнение ...', "tech": 'Установка пакета Vulkan GPU и модели речи ...'},
        'zh-TW': {"plain": '正在下載加速套件 ...', "tech": '正在安裝 Vulkan GPU 加速包與語音模型 ...'},
        'ja': {"plain": '高速化アドオンをダウンロード中 ...', "tech": 'Vulkan GPU パックと音声モデルをインストール中 ...'}},
    "ui.vk_offer_ok": {
        'en': {"plain": 'Speed-up add-on installed. Restart tlwu to use it.', "tech": 'Vulkan GPU pack installed. Restart tlwu to run speech and translation on the GPU.'},
        'ru': {"plain": 'Ускоряющее дополнение установлено. Перезапустите tlwu, чтобы оно заработало.', "tech": 'Пакет Vulkan GPU установлен. Перезапустите tlwu, чтобы речь и перевод шли на GPU.'},
        'zh-TW': {"plain": '加速套件已安裝，重新啟動 tlwu 後生效。', "tech": 'Vulkan GPU 加速包已安裝。重新啟動 tlwu 後，語音與翻譯會改用 GPU。'},
        'ja': {"plain": '高速化アドオンをインストールしました。tlwu を再起動すると有効になります。', "tech": 'Vulkan GPU パックをインストールしました。tlwu を再起動すると音声と翻訳が GPU で動きます。'}},
    "ui.vk_offer_ok_cpu": {
        'en': {"plain": 'Speed-up add-on installed. Restart tlwu to use it.', "tech": 'Vulkan GPU pack installed. Restart tlwu to use the GPU; {cpu} stays on the CPU.'},
        'ru': {"plain": 'Ускоряющее дополнение установлено. Перезапустите tlwu, чтобы оно заработало.', "tech": 'Пакет Vulkan GPU установлен. Перезапустите tlwu; {cpu} останется на CPU.'},
        'zh-TW': {"plain": '加速套件已安裝，重新啟動 tlwu 後生效。', "tech": 'Vulkan GPU 加速包已安裝。重新啟動 tlwu 後會使用 GPU；{cpu}仍使用 CPU。'},
        'ja': {"plain": '高速化アドオンをインストールしました。tlwu を再起動すると有効になります。', "tech": 'Vulkan GPU パックをインストールしました。tlwu を再起動すると GPU を使います。{cpu}は CPU のままです。'}},
    "ui.gpu_offer_ok": {
        'en': {"plain": 'Speed-up add-on installed. Restart tlwu to use it.', "tech": 'GPU speed-up pack installed. Restart tlwu to apply it.'},
        'ru': {"plain": 'Ускоряющее дополнение установлено. Перезапустите tlwu, чтобы оно заработало.', "tech": 'Пакет ускорения GPU установлен. Перезапустите tlwu, чтобы применить его.'},
        'zh-TW': {"plain": '加速套件已安裝，重新啟動 tlwu 後生效。', "tech": 'GPU 加速包已安裝。重新啟動 tlwu 後生效。'},
        'ja': {"plain": '高速化アドオンをインストールしました。tlwu を再起動すると有効になります。', "tech": 'GPU 高速化パックをインストールしました。tlwu を再起動すると反映されます。'}},
    "ui.vk_offer_fail": {
        'en': {"plain": 'The speed-up add-on did not install. Running at normal speed; translation still works.', "tech": 'Vulkan GPU pack not installed ({reason}). Speech and translation stay on the CPU.'},
        'ru': {"plain": 'Ускоряющее дополнение не установилось. Работает обычная скорость, перевод по-прежнему доступен.', "tech": 'Пакет Vulkan GPU не установлен ({reason}). Речь и перевод остаются на CPU.'},
        'zh-TW': {"plain": '加速套件沒裝成功，先用一般速度，翻譯照常可用。', "tech": 'Vulkan GPU 加速包未安裝（{reason}）。語音與翻譯仍使用 CPU。'},
        'ja': {"plain": '高速化アドオンをインストールできませんでした。通常の速さで動作し、翻訳はそのまま使えます。', "tech": 'Vulkan GPU パックをインストールできませんでした（{reason}）。音声と翻訳は CPU のままです。'}},
    "ui.gpu_offer_fail": {
        'en': {"plain": 'The speed-up add-on did not install. Running at normal speed; translation still works.', "tech": 'GPU pack not installed ({reason}). Translation stays on the CPU.'},
        'ru': {"plain": 'Ускоряющее дополнение не установилось. Работает обычная скорость, перевод по-прежнему доступен.', "tech": 'Пакет GPU не установлен ({reason}). Перевод остаётся на CPU.'},
        'zh-TW': {"plain": '加速套件沒裝成功，先用一般速度，翻譯照常可用。', "tech": 'GPU 加速包沒有安裝成功（{reason}），翻譯繼續使用 CPU。'},
        'ja': {"plain": '高速化アドオンをインストールできませんでした。通常の速さで動作し、翻訳はそのまま使えます。', "tech": 'GPU パックをインストールできませんでした（{reason}）。翻訳は CPU のままです。'}},
    "ui.gpu_auto_running": {
        'en': {"plain": 'Your computer can go faster. Downloading the speed-up add-on in the background ({size}); listening keeps going at normal speed ...', "tech": 'NVIDIA GPU found — downloading the speed-up pack in the background ({size}); listening keeps running on the CPU ...'},
        'ru': {"plain": 'Ваш компьютер может работать быстрее. Скачиваю ускоряющее дополнение в фоне ({size}); прослушивание идёт в обычном режиме ...', "tech": 'Найдена NVIDIA — пакет ускорения ({size}) скачивается в фоне; прослушивание работает на CPU ...'},
        'zh-TW': {"plain": '你的電腦可以更快 — 正在背景下載加速套件（約 {size}），聽譯照常以一般速度進行 ...', "tech": '偵測到 NVIDIA 顯示卡 — 正在背景下載 GPU 加速包（{size}），聆聽照常使用 CPU …'},
        'ja': {"plain": 'このパソコンはもっと速くできます。高速化アドオンをバックグラウンドでダウンロード中（約 {size}）。聞き取りは通常の速さで続きます ...', "tech": 'NVIDIA GPU を検出 — 高速化パック（{size}）をバックグラウンドでダウンロード中。聞き取りは CPU で継続します…'}},
    "ui.gpu_offer_running": {
        'en': {"plain": 'Installing the speed-up add-on ({size}) ...', "tech": 'Installing GPU speed-up pack ({size}) ...'},
        'ru': {"plain": 'Устанавливаю ускоряющее дополнение ({size}) ...', "tech": 'Установка пакета ускорения GPU ({size}) ...'},
        'zh-TW': {"plain": '正在安裝加速套件（{size}）...', "tech": '正在安裝 GPU 加速包（{size}）…'},
        'ja': {"plain": '高速化アドオンをインストール中（{size}）...', "tech": 'GPU 高速化パック（{size}）をインストール中…'}},
    "ui.gpu_pack_row_name": {
        'en': {"plain": 'Speed-up add-on ({size})', "tech": 'GPU speed-up pack (NVIDIA, {size})'},
        'ru': {"plain": 'Ускоряющее дополнение ({size})', "tech": 'Пакет ускорения GPU (NVIDIA, {size})'},
        'zh-TW': {"plain": '加速套件（{size}）', "tech": 'GPU 加速包（NVIDIA，{size}）'},
        'ja': {"plain": '高速化アドオン（{size}）', "tech": 'GPU 高速化パック（NVIDIA、{size}）'}},
    "ui.gpu_pack_row_unavailable": {
        'en': {"plain": 'Not available on this computer', "tech": 'Not available (no NVIDIA GPU)'},
        'ru': {"plain": 'Недоступно на этом компьютере', "tech": 'Недоступен (нет видеокарты NVIDIA)'},
        'zh-TW': {"plain": '這台電腦不適用', "tech": '無法使用（沒有 NVIDIA 顯示卡）'},
        'ja': {"plain": 'このパソコンでは使えません', "tech": '利用不可（NVIDIA GPU なし）'}},
    "ui.gpu_vulkan_ready": {
        'en': {"plain": 'Graphics chip: {name} (speed-up add-on installed)', "tech": 'Vulkan GPU: {name} (Vulkan pack installed)'},
        'ru': {"plain": 'Видеочип: {name} (ускоряющее дополнение установлено)', "tech": 'Видеокарта Vulkan: {name} (пакет Vulkan установлен)'},
        'zh-TW': {"plain": '顯示晶片：{name}（已安裝加速套件）', "tech": 'Vulkan 顯示卡：{name}（已安裝 Vulkan 加速包）'},
        'ja': {"plain": 'グラフィックチップ：{name}（高速化アドオン導入済み）', "tech": 'Vulkan GPU：{name}（Vulkan パック導入済み）'}},
    "ui.gpu_vulkan_nopack": {
        'en': {"plain": 'Graphics chip: {name} (speed-up add-on not installed or out of date)', "tech": 'Vulkan GPU: {name} (Vulkan pack not installed or out of date)'},
        'ru': {"plain": 'Видеочип: {name} (ускоряющее дополнение не установлено или устарело)', "tech": 'Видеокарта Vulkan: {name} (пакет Vulkan не установлен или устарел)'},
        'zh-TW': {"plain": '顯示晶片：{name}（加速套件未安裝或已過期）', "tech": 'Vulkan 顯示卡：{name}（Vulkan 加速包未安裝或已過期）'},
        'ja': {"plain": 'グラフィックチップ：{name}（高速化アドオン未導入または古い）', "tech": 'Vulkan GPU：{name}（Vulkan パック未導入または古い）'}},
    "ui.gpu_vulkan_unknown": {
        'en': {"plain": 'Graphics chip: {name}', "tech": 'Vulkan GPU: {name}'},
        'ru': {"plain": 'Видеочип: {name}', "tech": 'Видеокарта Vulkan: {name}'},
        'zh-TW': {"plain": '顯示晶片：{name}', "tech": 'Vulkan 顯示卡：{name}'},
        'ja': {"plain": 'グラフィックチップ：{name}', "tech": 'Vulkan 対応 GPU：{name}'}},
}
_TECH = {"flag": False, "setting": False}


def set_tech_labels(setting: bool | None = None, flag: bool | None = None) -> None:
    """The "Show technical details" setting and the --tech-labels launch flag; either one shows the tech text."""
    if setting is not None:
        _TECH["setting"] = bool(setting)
    if flag is not None:
        _TECH["flag"] = bool(flag)


def tech_labels() -> bool:
    return _TECH["flag"] or _TECH["setting"]


def label(key: str, lang: str | None = None, tech: bool | None = None) -> str:
    """#1806: the text of a LABELS key in the UI language: plain, or technical when the switch is on."""
    ui = lang or _ui()
    return LABELS[key][ui]["tech" if (tech_labels() if tech is None else tech) else "plain"]


def ui_text(key: str, **kw) -> str:
    """#1807: a settings / status text by its i18n key: plain or tech from LABELS (`ui.<key>`), else plain t(key)."""
    from ..i18n import t

    if f"ui.{key}" not in LABELS:
        return t(key, **kw)
    s = label(f"ui.{key}")
    return s.format(**kw) if kw else s


def label_texts(key: str, lang: str) -> tuple[str, ...]:
    return tuple(LABELS[key][lang][v] for v in LABEL_VARIANTS)


_LABEL_KINDS = ("quality",)  # dropdown kinds whose names live in LABELS (#1806); the rest are still in _CHOICES


def _ui() -> str:
    from ..i18n import ui_lang

    u = ui_lang()
    return u if u in _MARK_MISSING else "en"


def choice_label(kind: str, value: str, lang: str | None = None) -> str:
    """Name shown in a dropdown for a stored value (#884); target languages use the script name only."""
    from .. import langs

    ui = lang or _ui()
    if kind == "target":
        return langs.plain_label(value, ui)
    if kind in _LABEL_KINDS:
        return label(f"{kind}.{value}", ui) if f"{kind}.{value}" in LABELS else value
    return _CHOICES[kind][ui].get(value, value)


PAIR_MAX_PX = 120  # #884: widest pair label (9 pt) on the 420 px bar (#455): with it the bar measured <= 402 px
# under xvfb (Inter 10 pt), and CI fonts ran ~12 px wider than that box on PR #900 (430 -> 442)
_PAIR_SHORT = {"en": {"zh-tw": "Chinese (Trad.)", "zh-cn": "Chinese (Simp.)"},
               "ru": {"zh-tw": "китайский (трад.)", "zh-cn": "китайский (упр.)"},
               "zh-TW": {}, "ja": {"zh-tw": "中国語（繁体）", "zh-cn": "中国語（簡体）"}}


def pair_text(src: str, target: str, measure, lang: str | None = None, max_px: int = PAIR_MAX_PX) -> str:
    """Bar label for a direction (#884): names, never codes. One line when it fits in max_px, else the target on
    a second line, else a shorter target name (still the script name, never a region). measure(text) -> pixels."""
    from .. import langs

    ui = lang or _ui()
    a = langs.plain_label(src, ui)
    for b in (choice_label("target", target, ui), _PAIR_SHORT.get(ui, {}).get(target)):
        if not b:
            continue
        one = f"{a}\u2192{b}"
        if measure(one) <= max_px:
            return one
        two = f"{a} \u2192\n{b}"
        if max(measure(line) for line in two.split("\n")) <= max_px:
            return two
    return two


def choice_labels(kind: str, values, lang: str | None = None) -> list[str]:
    return [choice_label(kind, v, lang) for v in values]


def choice_value(kind: str, label: str, values) -> str:
    """The stored value for a dropdown entry in any UI language (also accepts the raw value)."""
    label = (label or "").strip()
    for v in values:
        if label == v or any(label == choice_label(kind, v, u) for u in _MARK_MISSING):
            return v
        if kind in _LABEL_KINDS and f"{kind}.{v}" in LABELS and any(
                label in label_texts(f"{kind}.{v}", u) for u in _MARK_MISSING):
            return v  # a saved name from the other view (plain <-> tech) still parses
    return label


def cpu_ram_gb(has_cuda, ram=None) -> float | None:
    """#780: RAM in GB for the model picker's "too heavy" marks; None (no marks) when a CUDA device is present."""
    from ..hwprobe import ram_mib

    if has_cuda:
        return None
    mib = (ram or ram_mib)()
    return None if mib is None else mib / 1024


def mt_path_text(path: tuple[str, int] | None) -> str:
    """#1198: where Hy-MT2 runs, e.g. "Hy-MT2 on CPU, 4 threads" / "Hy-MT2 on GPU (Vulkan)"; "" when unknown."""
    from ..i18n import t

    if not path:
        return ""
    backend, threads = path
    if backend == "cpu":
        return t("gui_mt_path_cpu", n=threads) if threads > 0 else t("gui_mt_path_cpu_plain")
    return ui_text("gui_mt_path_gpu", api={"vulkan": "Vulkan", "cuda": "CUDA"}.get(backend, backend))


_NVIDIA_SEEN: list[bool] = []  # cache: nvidia-smi runs at most once per process


def nvidia_seen(gpu_info=None, block: bool = True) -> bool:
    """#1525: nvidia-smi reports an NVIDIA GPU (cached). CPU mode on such a machine must not say "no NVIDIA GPU".
    block=False (Tk thread): never runs nvidia-smi; an unknown result reads as False (the GUI probes at start-up)."""
    if gpu_info is not None:
        return gpu_info()[0] is not None
    if not _NVIDIA_SEEN and not block:
        return False
    if not _NVIDIA_SEEN:
        from ..hwprobe import gpu_info as probe

        try:
            _NVIDIA_SEEN.append(probe()[0] is not None)
        except Exception:  # noqa: BLE001 - no probe = say nothing about the GPU
            _NVIDIA_SEEN.append(False)
    return _NVIDIA_SEEN[0]


def cpu_mode_text(gpu_seen: bool | None = None, block: bool = True) -> str:
    """The "CPU mode" text: "no NVIDIA GPU" only when nvidia-smi found none, else "NVIDIA GPU found but not used"."""
    return ui_text("gui_cpu_mode_gpu_unused" if (nvidia_seen(block=block) if gpu_seen is None else gpu_seen)
                   else "gui_cpu_mode")


def cpu_mode_line(device: str, mt_ms: float | None = None, lang: str | None = None,
                  path: tuple[str, int] | None = None, block: bool = True) -> str | None:
    """#780: status text "CPU mode" (+ per-line MT ms) when running on the CPU, else None.
    path (#1198): (backend, threads) of the MT model, shown after the ms so the user sees CPU vs Vulkan."""
    from ..i18n import t

    if device != "cpu":
        return None
    base = cpu_mode_text(block=block)
    if mt_ms is None:
        return base
    where = mt_path_text(path)
    return f"{base} - {t('gui_cpu_mt_ms', ms=round(mt_ms))}" + (f" ({where})" if where else "")


def drop_cpu_mode_notice(notice: str, status: str) -> str:
    """#1241: the CPU-mode text is shown once. When the status line already carries it (with the per-line MT ms),
    remove it from the orange notice (a whole line, or a " | ..." tail); other notice text stays."""
    from ..i18n import t

    cpus = {t("gui_cpu_mode"), t("gui_cpu_mode_gpu_unused")}  # #1525: either wording
    for k in ("gui_cpu_mode", "gui_cpu_mode_gpu_unused"):  # #1807: and either view (plain / tech)
        cpus.update(label_texts(f"ui.{k}", _ui()))
    if not notice or not any(c in (status or "") for c in cpus):
        return notice
    keep = []
    for ln in notice.split(chr(10)):
        parts = [p for p in ln.split(" | ") if p.strip() not in cpus]
        if parts:
            keep.append(" | ".join(parts))
    return chr(10).join(keep).strip()


def model_labels(mdir=None, ram_gb: float | None = None) -> list[str]:
    """Dropdown entries: downloaded Whisper models first, marked with a check; the rest plain (#739: a missing model
    downloads on the first Start with a progress bar, so the list shows no "not downloaded" state).
    ram_gb (CPU mode, #780): heavy models get a "too heavy for this PC" mark when RAM is below 24 GB."""
    from ..hwprobe import too_heavy
    from ..models import whisper_installed

    have = whisper_installed(mdir)
    heavy = lambda m: _MARK_HEAVY if too_heavy(m, ram_gb) else ""  # noqa: E731
    return ([_MARK_OK + m + heavy(m) for m in MODELS if m in have]
            + [m + heavy(m) for m in MODELS if m not in have])  # #739: a missing model downloads on the first Start


_ENGINE_DIR = {"hymt": "hymt2-1.8b", "hymt7b": "hymt2-7b"}  # models/<dir> per engine (auto needs no model of its own); #471: no opus
_ENGINE_HINT = {"hymt": "tlwu-setup --hymt", "hymt7b": "tlwu-setup --hymt7b"}


def engine_labels(mdir=None) -> list[str]:
    """#399: Advanced engine entries; an engine whose model is not installed carries the setup command to get it."""
    from ..models import models_dir

    d = models_dir(str(mdir) if mdir else None)
    return [choice_label("engine", e) if e == "auto" or (d / _ENGINE_DIR[e]).is_dir() else f"{e} ({_ENGINE_HINT[e]})"
            for e in ENGINES]


def engine_model_missing(s: dict, mdir=None) -> str | None:
    """#1351: the localized line to show instead of starting when Quality is Manual, Engine is the opt-in hymt7b and
    its model is not downloaded (the CLI would exit with a setup command the GUI never shows, #885); else None.
    hymt (1.8B) is not checked: --mt-pending shows source text while it downloads (#1375)."""
    from ..hymt import hymt_path
    from ..i18n import t
    from ..models import models_dir

    if s.get("quality") != "manual" or s.get("engine") != "hymt7b":
        return None
    try:
        if hymt_path(models_dir(str(mdir) if mdir else None), "7b").is_file():  # the file make_translator loads
            return None
    except Exception:  # noqa: BLE001 - an unreadable path: let the run report its own error
        return None
    return t("gui_error_hymt7b_missing")


def engine_from_label(label: str) -> str:
    """The plain engine id from an engine_labels() entry (also accepts a plain id)."""
    return choice_value("engine", (label or "").split(" (", 1)[0].strip(), ENGINES)


def model_from_label(label: str) -> str:
    """The plain model name from a dropdown entry (also accepts a plain name)."""
    label = label.replace(_MARK_OK, "", 1).replace(_MARK_HEAVY, "")
    for mark in (_MARK_MISSING_OLD, *_MARK_MISSING.values()):  # old saved labels (#739 / #884)
        label = label.replace(mark, "")
    return label.strip()


def label_for_model(name: str, mdir=None, ram_gb: float | None = None) -> str:
    return next((l for l in model_labels(mdir, ram_gb) if model_from_label(l) == name), name)


def hw_tier_name(probe=None) -> str | None:
    """"gpu" / "cpu" from the hardware probe (#63), None when the probe fails (never stops a session)."""
    try:
        from ..hwprobe import probe as _probe, recommend

        return recommend((probe or _probe)()).name
    except Exception:  # noqa: BLE001
        return None


def is_downloaded(model: str, mdir=None) -> bool:
    from ..models import whisper_installed

    return model in whisper_installed(mdir)


def usable_model(wanted: str | None, mdir=None, tier=hw_tier_name) -> str:
    """The chosen Whisper model, or the best downloaded one for this PC's hardware tier when it is not on disk
    (#96). The probe (nvidia-smi) only runs when the chosen model is missing; `tier` = a name or a callable."""
    from ..models import pick_whisper

    wanted = wanted or "small"
    if is_downloaded(wanted, mdir):
        return wanted
    return pick_whisper(wanted, mdir, tier() if callable(tier) else tier)


def text_width(s: str) -> int:
    """Width in character cells (CJK = 2), for sizing a ttk.Combobox (`width` counts average characters)."""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def combo_width(values, minimum: int = 18) -> int:
    return max([minimum] + [text_width(v) + 1 for v in values])


def download_cmd(model: str, mdir=None, bundled: bool | None = None) -> list[str]:
    """Child process that downloads one Whisper model (#96 item 3). The network code lives in tinglongwu_setup and
    runs only in this separate process after the user said yes; this package never downloads anything itself.
    Bundled tlwu.exe: `tlwu.exe --run-setup ...` (tlwu_app.main); from source: `python -m tinglongwu_setup ...`."""
    from ..models import models_dir

    if bundled is None:
        from .autoupdate import is_bundled  # #1007: also tlwu.exe as sys.executable (launcher env not in os.environ)

        bundled = is_bundled()
    base = [os.path.abspath(sys.argv[0]), "--run-setup"] if bundled else [sys.executable, "-m", "tinglongwu_setup"]
    return [*base, "--whisper", model, "--models-dir", str(models_dir(str(mdir) if mdir else None))]


def pack_download_cmd(pack: str, mdir=None, bundled: bool | None = None) -> list[str]:
    """#673: same child as download_cmd, but `--packs <lang>` (resumable, SHA256-checked) instead of `--whisper`."""
    cmd = download_cmd("small", mdir, bundled)
    i = cmd.index("--whisper")
    return [*cmd[:i], "--packs", pack, *cmd[i + 2:]]


def download_env(env=None) -> dict:
    """Environment for the download child: this process's offline flags (HF_HUB_OFFLINE etc.) removed."""
    from ..offline import OFFLINE_ENV

    out = {k: v for k, v in (os.environ if env is None else env).items() if k not in OFFLINE_ENV}
    out.update(PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
    return out


def short_line(line: str, limit: int = 90) -> str:
    """#918: one child output line cut to `limit` chars for the status text. A tqdm bar field ("|####      |") is
    dropped first and a long file name is cut to 30 chars ("model-00001-...0002.safetensors"), so the
    "NN% 48M/484M" part stays readable."""
    import re

    line = re.sub(r"\|[^|]*\|", "|", line)
    m = list(re.finditer(r"\d{1,3}\s*%", line))
    if not m:
        return line[:limit]
    head = line[:m[-1].start()]
    name = head.rstrip()  # tqdm pads " 10%" vs "100%": cut the name, not the padding
    if len(name) > 30:  # display only: StartDownload tracks by the full line (line_event)
        name = f"{name[:12]}...{name[-15:]}"
    return (name + head[len(head.rstrip()):] + line[m[-1].start():])[:limit]


def line_event(kind: str, line: str) -> tuple:
    """#918: (kind, status text) for a child output line, plus the full line as a third item when the text was
    shortened, so StartDownload tracks each file by its real name (consumers that only read ev[1] are unchanged)."""
    text = short_line(line)
    return (kind, text) if text == line else (kind, text, line)


def run_download(model: str, put, mdir=None, popen=None, bundled: bool | None = None) -> int:
    """Run the download child (worker thread); `put` gets ("dl_line", text) per output line and
    ("dl_done", model, returncode). Returns the return code (-1 if it could not start)."""
    import subprocess

    popen = popen or subprocess.Popen
    try:
        p = popen(download_cmd(model, mdir, bundled), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                  env=download_env(), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        for raw in p.stdout:
            line = (raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw).replace("\r", "\n")
            parts = [x.strip() for x in line.split("\n") if x.strip()]
            if parts:
                put(line_event("dl_line", parts[-1]))
        rc = p.wait()
    except Exception as e:  # noqa: BLE001
        put(("dl_line", f"{type(e).__name__}: {e}"[:90]))
        rc = -1
    put(("dl_done", model, rc))
    return rc


def parse_progress(line: str) -> int | None:
    """#739: the last "NN%" in a download child's output line (0-100), else None."""
    import re

    m = re.findall(r"(\d{1,3})\s*%", line or "")
    return min(100, int(m[-1])) if m else None


def missing_start_model(run: dict, mdir=None, want: str | None = None) -> str | None:
    """#739: the Whisper model a Start needs but does not have on disk, else None. Manual quality: the picked model.
    #1385: the other qualities: `want`, the model quality_speech_model() named in the Quality hint (Accurate on a
    fresh install needs a larger model than the one setup fetched; before, the CLI silently kept the small one)."""
    if run.get("quality") != "manual":
        return None if not want or is_downloaded(want, mdir) else want
    m = run.get("model") or "small"
    return None if is_downloaded(m, mdir) else m


# #1385: approximate first download per speech model in MB, shown before a Start downloads it. small and
# large-v3-turbo = tlwu_app/tiers.py SIZES (464 / 1640 MiB); the others = model.bin of the faster-whisper repos
# pinned in tinglongwu_setup/manifest.json (tiny 75, base 145, medium 1530, large-v3 3090 MB on Hugging Face).
WHISPER_MB = {"tiny": 75, "base": 145, "small": 486, "medium": 1530, "large-v3": 3090, "large-v3-turbo": 1720}
# #1385: plain words per speech model; CPU and GPU differ (turbo is the GPU default but slow on a CPU, #63 / #131)
_ASR_WORDS_CPU = {"tiny": "gui_asr_fast", "base": "gui_asr_fast", "small": "gui_asr_balanced"}
_ASR_WORDS_GPU = {"tiny": "gui_asr_fast", "base": "gui_asr_fast", "small": "gui_asr_fast",
                  "medium": "gui_asr_balanced", "large-v3-turbo": "gui_asr_balanced"}


def quality_speech_model(level: str, cuda: bool, hw=None, mdir=None, manual_model: str | None = None,
                         want=None) -> str:
    """#1385: the speech model a Start with this Quality uses on this PC. Manual: the picked model. Accurate: the
    tier's model even when it is not downloaded yet (the Start downloads it, missing_start_model). Fast / Auto /
    Balanced: what the CLI's auto_model picks (the tier's model, else the best downloaded one).
    `want` = cli.want_model (injected in tests); `hw` = hwprobe.probe() result, probed when None."""
    from ..models import pick_whisper

    if level == "manual":
        return manual_model or "small"
    if want is None:
        from ..cli import want_model as want
    model, tier = want(level, "cuda" if cuda else "cpu", hw)
    return model if level == "quality" else pick_whisper(model, mdir, tier)


def download_size_text(model: str) -> str:
    """#1385: "about 1.7 GB" style size of a speech model's first download ("" when unknown)."""
    mb = WHISPER_MB.get(model)
    if mb is None:
        return ""
    return f"{mb / 1000:.1f} GB" if mb >= 1000 else f"{mb} MB"


def quality_hint(model: str, cuda: bool, downloaded: bool) -> str:
    """#1385: the line under the Speed buttons: which speech model runs, in plain words, and (before any download)
    how big its download is. Example: "Speech model: large-v3-turbo (most accurate, slower on this PC). The next
    Start downloads it first (about 1.7 GB)." """
    from ..i18n import t

    words = (_ASR_WORDS_GPU if cuda else _ASR_WORDS_CPU).get(model, "gui_asr_accurate" if cuda else "gui_asr_slow")
    line = t("gui_asr_line", model=model, words=t(words))
    size = download_size_text(model)
    if downloaded:
        return f"{line} {t('gui_asr_have')}"
    return f"{line} {t('gui_asr_download', size=size) if size else t('gui_asr_download_unknown')}"


_UNIT = {"": 1, "k": 1e3, "m": 1e6, "g": 1e9, "t": 1e12}


def progress_bar(line: str) -> tuple[str, int, float] | None:
    """#918: (label, percent, total) of one download progress line, else None. label is the text before the last
    "NN%" ("model.bin" in "model.bin: 10%|#| 48M/484M"); total is the size after the "/" that follows it
    (484e6 there, 250e6 for "(87/250 MB)", 4 for tqdm's "Fetching 4 files: 25%| 1/4"), 0 when none is printed."""
    import re

    line = line or ""
    m = list(re.finditer(r"(\d{1,3})\s*%", line))
    if not m:
        return None
    last = m[-1]
    label = line[:last.start()].strip().rstrip(":").strip()
    t = re.search(r"\d\s*[kKMGT]?i?B?\s*/\s*(\d+(?:\.\d+)?)\s*([kKMGT]?)", line[last.end():])
    total = float(t.group(1)) * _UNIT[t.group(2).lower()] if t else 0.0
    return label, min(100, int(last.group(1))), total


class StartDownload:
    """#739: progress of the download that runs on the first Start. Feed it the `put` events of run_download /
    run_pack_download; `ok` is set on a zero return code, `error` is one line on failure.
    #918: huggingface_hub prints one tqdm bar per file, so `percent` is the bar of the largest file seen (by the
    total it prints), else of the latest line's label; each label's percent only goes up."""

    def __init__(self, name: str):
        self.name, self.percent, self.done, self.ok, self.error, self._last = name, 0, False, False, "", ""
        self._bars: dict[str, list] = {}  # label -> [percent, total]

    def feed(self, ev: tuple) -> None:
        kind = ev[0]
        if kind in ("dl_line", "pack_line"):
            self._last = ev[1]
            bar = progress_bar(ev[2] if len(ev) > 2 else ev[1])  # #918: the full line when the text was shortened
            if bar is not None:
                label, p, total = bar
                b = self._bars.pop(label, [0, 0.0])
                b[0], b[1] = max(b[0], p), max(b[1], total)
                self._bars[label] = b  # latest last: equal totals -> the bar updated most recently
                big = max(reversed(self._bars.values()), key=lambda x: x[1])
                self.percent = big[0] if big[1] > 0 else b[0]
        elif kind in ("dl_done", "pack_done"):
            self.done, self.ok = True, ev[2] == 0
            if self.ok:
                self.percent, self.error = 100, ""
            else:
                self.error = (self._last or f"download failed (exit {ev[2]})").splitlines()[-1][:90]


def run_pack_download(pack: str, put, mdir=None, popen=None, bundled: bool | None = None, cmd=None,
                      on_start=None) -> int:
    """Worker thread: run the pack download child; `put` gets ("pack_line", text) per line and ("pack_done", pack, rc).
    on_start(proc): #1379: gets the child right after it started, so the GUI can cancel it (proc.terminate())."""
    import subprocess

    popen = popen or subprocess.Popen
    p = None
    try:
        p = popen(cmd or pack_download_cmd(pack, mdir, bundled), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                  env=download_env(), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if on_start is not None:
            on_start(p)
        for raw in p.stdout:
            line = (raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw).replace("\r", "\n")
            parts = [x.strip() for x in line.split("\n") if x.strip()]
            if parts:
                put(line_event("pack_line", parts[-1]))
        rc = p.wait()
    except Exception as e:  # noqa: BLE001
        put(("pack_line", f"{type(e).__name__}: {e}"[:90]))
        rc = -1
        if p is not None:  # #1525: never return while the child may still write (callers release their lock here);
            try:  # let it finish rather than kill it mid-swap (a kill between the two renames loses the old pack)
                p.wait()
            except Exception:  # noqa: BLE001
                _diag.swallowed(__name__)
    put(("pack_done", pack, rc))
    return rc


def topics_download_cmd(mdir=None, bundled: bool | None = None) -> list[str]:
    """#1379: same child as download_cmd, but `--topics` (the pinned meeting-notes GGUF) instead of `--whisper`."""
    cmd = download_cmd("small", mdir, bundled)
    i = cmd.index("--whisper")
    return [*cmd[:i], "--topics", *cmd[i + 2:]]


def voice_install_cmd(mdir=None, bundled: bool | None = None, ru: bool = False) -> list[str]:
    """#967: same child as download_cmd, but `--talk` (Kokoro venv + model + smoke, the CLI's step) instead of `--whisper`.
    ru: the Russian voice instead: `--piper-engine --piper-ru` (piper-tts into the TTS venv + the dmitri voice files)."""
    cmd = download_cmd("small", mdir, bundled)
    i = cmd.index("--whisper")
    return [*cmd[:i], *(["--piper-engine", "--piper-ru"] if ru else ["--talk"]), *cmd[i + 2:]]


def run_voice_install(put, mdir=None, popen=None, bundled: bool | None = None, ru: bool = False) -> int:
    """#967: worker thread: run the voice install child; same events as run_pack_download (pack "voice" / "voice-ru")."""
    return run_pack_download("voice-ru" if ru else "voice", put, mdir, popen, bundled,
                             cmd=voice_install_cmd(mdir, bundled, ru))


# #1379: the meeting-notes model the #1378 bake-off picked (models/manifest.json key, pinned in tinglongwu_setup).
TOPICS_KEY = "topics-qwen3-4b-2507"  # keep in sync with tinglongwu_setup.downloader.TOPICS_DIR
TOPICS_FILE = "Qwen3-4B-Instruct-2507-Q4_K_M.gguf"
TOPICS_MB = 2500  # 2 497 281 120 bytes (#1381 pin)


def default_topics_model(mdir=None) -> str:
    """#1379: path of the downloaded default notes model, or "" when it is not installed (the file exists AND the
    local models/manifest.json pinned it: a half-finished download only leaves a .part file and no pin)."""
    from ..models import models_dir

    d = Path(models_dir(str(mdir) if mdir else None))
    try:
        pinned = TOPICS_KEY in json.loads((d / "manifest.json").read_text("utf-8"))
    except (OSError, ValueError):
        return ""
    f = d / TOPICS_KEY / TOPICS_FILE
    return str(f) if pinned and f.is_file() else ""


def topics_model_path(s: dict, mdir=None) -> str:
    """#1379: the notes model for these settings: a GGUF the user picked, else the downloaded default, else ""."""
    model = s.get("topics_model") or ""
    if model and os.path.isfile(model):
        return model
    return default_topics_model(mdir)


def topics_on(s: dict, has_cuda=None, mdir=None) -> bool:
    """#611: run topic notes for these settings? Needs a model file; an unset choice follows topics_default
    (on with a CUDA device, off on CPU-only). has_cuda: None = probe."""
    if not topics_model_path(s, mdir):
        return False
    if s.get("topics") is not None:
        return bool(s["topics"])
    from .topics_panel import topics_default

    return topics_default(_gpu_usable() if has_cuda is None else has_cuda)


def settings_to_argv(s: dict, outputs: list[str] | None = None, notice=None) -> list[str]:
    """Map GUI settings to CLI args (so the GUI reuses the CLI's parser and defaults).
    outputs: the current voice_outputs() (#448): a saved voice_device that is no longer listed is left out (default
    output) and notice(str) gets one line; None or [] (list unknown / no audio stack) passes the saved name on."""
    to, variant = {"zh-tw": ("zh", "tw"), "zh-cn": ("zh", "cn"), "en": ("en", "tw")}.get(s.get("target"), ("zh", "tw"))
    if s.get("target") in SOURCE_LANGS and s.get("target") not in ("auto", "zh"):  # #737: speak -> the other side (ja, ru, ...)
        to = s["target"]
    argv = ["--lang", s.get("lang") or "auto", "--to", to, "--zh-variant", variant,
            "--preset", s.get("preset") if s.get("preset") in PRESETS else "meeting"]
    quality = s.get("quality") if s.get("quality") in QUALITIES else DEFAULTS["quality"]
    if quality == "manual":
        engine = s.get("engine") if s.get("engine") in ENGINES else "auto"  # #471: a removed engine (opus) -> auto
        argv += ["--engine", engine, "--model", usable_model(s.get("model"))]
    else:  # the CLI picks engine + speech model from the hardware probe
        argv += ["--quality", quality]
    if s.get("gpu_use") == "off":  # #1531: the manual switch, only under Advanced; auto = the CLI falls back to CPU itself
        argv += ["--device", "cpu"]
    argv += ["--mt-pending"]  # #1375: Hy-MT2 still downloading -> source-text subtitles, translation switches on later
    if s.get("source") == "mic":
        argv += ["--mic"]
        if s.get("mic_device"):  # #894: a microphone picked in Advanced; default = the system default mic
            argv += ["--capture-device", s["mic_device"]]
    elif s.get("listen_device"):  # #1070: an output picked in Advanced; default = every output but our cable
        argv += ["--capture-device", s["listen_device"]]
    if s.get("glossary"):
        argv += ["--glossary", s["glossary"]]
    notes = topics_on(s)
    if s.get("save") or notes:  # #1379: the notes switch alone saves the transcript (--topics needs --save)
        argv += ["--save"] + ([s["save_path"]] if s.get("save_path") else [])
    if notes:
        argv += ["--topics", topics_model_path(s)]
    if s.get("voice"):  # #330: the CLI checks the output is not the captured device and exits with a reason
        dev = s.get("voice_device") or ""
        # #448: unplugged since it was saved. #710: the Speak cable is not in voice_outputs() (cables are hidden
        # from the earphone list) but was just found by meeting_cable(), so it is passed on as is.
        if dev and outputs and dev not in outputs and not s.get("speak_cable"):
            if notice is not None:
                from ..i18n import t

                notice(t("gui_voice_device_gone", name=dev))
            dev = ""
        argv += ["--voice"] + (["--voice-device", dev] if dev else [])
    return argv


def voice_outputs(names=None) -> list[str]:
    """#330: output devices offered for the GUI voice: real speakers/earphones only (virtual cables, BlackHole,
    tlwu_sink and monitors are left out, the listen side could record them). names=None lists soundcard's."""
    from ..voice import voice_output_problem

    if names is None:
        try:
            import soundcard as sc

            names = [sp.name for sp in sc.all_speakers()]
        except Exception:  # noqa: BLE001 - no audio stack: only "default output" is offered
            names = []
    return [n for n in names if n and voice_output_problem(n, None) is None]


def mic_inputs(names=None) -> list[str]:
    """#894: microphones offered for the mic source (Advanced): real inputs only, never the VB-CABLE capture side,
    BlackHole, our tlwu_ virtual mic or a monitor (they carry our own voice or the PC sound, not the room).
    names=None lists soundcard's."""
    from ..reverse.live import is_virtual_mic

    if names is None:
        try:
            import soundcard as sc

            names = [m.name for m in sc.all_microphones(include_loopback=False)]
        except Exception:  # noqa: BLE001 - no audio stack: only "default microphone" is offered
            names = []
    return [n for n in names if n and not is_virtual_mic(n)]


def listen_outputs(names=None) -> list[str]:
    """#1070: outputs the listen leg may be pointed at (Advanced): every real output, never our own cable / sink."""
    from ..capture.wasapi import listen_names

    if names is None:
        try:
            import soundcard as sc

            names = [sp.name for sp in sc.all_speakers()]
        except Exception:  # noqa: BLE001 - no audio stack: only "all outputs" is offered
            names = []
    return listen_names(list(names))


class QueueSink(Sink):
    """Final lines -> queue (runs on the worker thread; never touches tkinter)."""

    def __init__(self, q: queue.Queue):
        self.q = q

    def write_source(self, src: str, original: str, speaker: str | None = None) -> None:
        self.q.put(("source", original))  # shown dim until its "final" arrives (#53)

    prefix = ""  # OverlaySink: put before each translation on the bar

    def write_partial(self, r: Result) -> None:
        """#1771: the translation so far of a line still being translated; its "final" replaces it in place."""
        if r.src != r.tgt and r.target:
            self.q.put(("partial", r.original, self.prefix + r.target))

    def write(self, r: Result) -> None:
        from ..reverse.echoguard import shared

        if shared().check(r.original, r.target if r.src != r.tgt else ""):  # #1070: our own voice coming back
            self.q.put(("drop", r.original))  # #1532: its pending row (if shown) goes away instead of waiting forever
            return
        ev = ("final", r.original, r.target if r.src != r.tgt and r.target else r.original)
        if r.replaces is not None:  # #1395: the previous line + a late fragment, shown in that line's place
            ev = ("amend", r.replaces) + ev[1:]
        self.q.put(ev + (r.speaker,) if r.speaker else ev)  # 0.3.8: the async speaker token rides along


def wrap_bubble(text: str, measure, max_px: int) -> list[str]:
    """#1070: break `text` into lines that are at most max_px wide (measure(str) -> pixels): at spaces, and between
    CJK characters; a single over-long word is split by characters. Always at least one line."""
    import re

    tokens = re.findall(r"[぀-ヿ㐀-鿿가-힯]|[^\s぀-ヿ㐀-鿿가-힯]+|\s+", text.replace(chr(10), " "))
    lines: list[str] = []
    cur = ""
    for tok in tokens:
        if tok.isspace():
            cur += " " if cur else ""
            continue
        if cur and measure(cur + tok) > max_px:
            lines.append(cur.rstrip())
            cur = ""
        while measure(tok) > max_px and len(tok) > 1:  # one word wider than the bubble
            k = len(tok) - 1
            while k > 1 and measure(tok[:k]) > max_px:
                k -= 1
            lines.append(tok[:k])
            tok = tok[k:]
        cur += tok
    if cur.strip():
        lines.append(cur.rstrip())
    return lines or [""]


def _zh_display(text: str, variant: str) -> str:
    """#1070: Whisper writes Chinese in Simplified; show it in the user's script. Falls back to the text as is."""
    try:
        from ..translate import zh_convert

        return zh_convert(text, variant)
    except Exception:  # noqa: BLE001 - opencc missing: show what Whisper wrote
        return text


class SpeakQueueSink(QueueSink):
    """#1001: the speak leg of a combined session: finished lines only ("final_side", marked with SPEAK_MARK), so
    they share the subtitle list with the listen leg without touching its draft / pending rows."""

    def write_source(self, src: str, original: str, speaker: str | None = None) -> None:
        pass

    def write_partial(self, r: Result) -> None:
        pass  # #1771: the speak leg shows finished lines only (it shares the list with the listen leg's rows)

    variant = "tw"  # #1070: the script my own words are shown in (zh-tw -> Traditional)

    def write(self, r: Result) -> None:
        out = r.target if r.src != r.tgt and r.target else r.original
        original = r.original
        if lang_base(r.src) == "zh":
            original = _zh_display(original, self.variant)
            if r.src == r.tgt or not r.target:
                out = original
        from ..reverse.echoguard import shared

        shared().note(r.original)  # #1070: the source of what we send, for the listen-leg echo guard
        self.q.put(("final_side", SPEAK_MARK + original, SPEAK_MARK + out))


class QueueDraftRenderer(DraftRenderer):
    """Drop-in for DraftRenderer that publishes draft events instead of writing ANSI to a terminal."""

    def __init__(self, q: queue.Queue):
        super().__init__(stream=None, columns=lambda: 100, enabled=True)
        self.q = q

    def clear(self) -> None:
        self.q.put(("clear",))

    def draw(self, src_tag, confirmed, tail, tgt_tag="", translation="", show_src=True) -> None:
        src = (confirmed + (" " + tail if confirmed and tail and " " in (confirmed + tail) else tail)).strip()
        if translation:
            self.q.put(("draft", src, translation))
        elif show_src:  # same-language / transcript-only: the source itself is the draft
            self.q.put(("draft", "", src))


def strip_silent_hint(text: str) -> str:
    """#1070/0.3.8: drop the "10 s without the other side's sound" line from a notice (any UI language); other
    notice lines stay. Called when listen-side audio or an other-side bubble arrives."""
    from ..i18n import MESSAGES

    heads = {m[k].split("{names}")[0] for m in MESSAGES.values() for k in ("listen_silent", "listen_silent_mic") if k in m}
    keep = [ln for ln in (text or "").split(chr(10)) if not any(ln.startswith(h) for h in heads)]
    return chr(10).join(keep).strip()


def speaker_text(book, label, fmt):
    """0.3.8: what a 對方 bubble's label shows: the user's name for that speaker, else "Speaker N" (fmt(n) in the UI
    language). None = no label. `book` is the session's speakers.SpeakerBook."""
    from ..speakers import LABEL_PREFIX

    shown = book.display(label) if label else None
    if not shown:
        return None
    if shown.startswith(LABEL_PREFIX) and shown[len(LABEL_PREFIX):].isdigit():
        return fmt(int(shown[len(LABEL_PREFIX):]))
    return shown


PENDING_MARK = " …"  # #1532: a heard line whose translation is still running: dim source text + this mark


class SubtitleState:
    """What the overlay shows: last `max_lines` lines; a draft is replaced in place by the final.

    #1532: a "source" event (ASR has the text, MT still running) adds the line at once, in its place, as a pending
    row (dim source + PENDING_MARK); its "final" fills that same row in place, so no other line moves and a
    translation that comes back out of order still lands on its own line."""

    def __init__(self, max_lines: int = 2):
        self.max_lines = max_lines
        # (src, target), oldest first; target None = pending (#1532: shown as dim source until its final arrives)
        self.finals: list[tuple[str, str | None]] = []
        self.draft: tuple[str, str] | None = None
        self.partial: dict[str, str] = {}  # #1771: source of a pending row -> its translation so far
        self.whos: list[str | None] = []  # 0.3.8: speaker token of each final (aligned with finals; None = none)
        self.paused = False  # frozen display: events still update the state (nothing is lost) but nothing is redrawn

    @property
    def pending(self) -> list[str]:
        """#53/#1532: sources heard whose translation is still running, oldest first."""
        return [src for src, tgt in self.finals if tgt is None]

    def apply(self, ev: tuple) -> bool:
        """Apply an event; True if the display should be redrawn."""
        if self._apply(ev):
            return not self.paused
        return False

    def _append(self, src: str, tgt: str | None, who: str | None) -> None:
        self.finals.append((src, tgt))
        self.whos.append(who)
        del self.finals[:-self.max_lines], self.whos[:-self.max_lines]

    def _apply(self, ev: tuple) -> bool:
        kind = ev[0]
        if kind == "draft":
            self.draft = (ev[1], ev[2])
        elif kind == "source":
            self._append(ev[1], None, None)
        elif kind == "partial":  # #1771 ("partial", src, text so far): fills the pending row (or the draft row)
            if any(tgt is None and src == ev[1] for src, tgt in self.finals):
                self.partial[ev[1]] = ev[2]
            else:  # no pending row (--stream drafts, or scrolled out): the draft row shows it until the final
                self.draft = (ev[1], ev[2])
        elif kind == "final":
            self.draft = None  # a live draft is replaced by its final
            self.partial.pop(ev[1], None)
            who = ev[3] if len(ev) > 3 else None
            i = next((k for k, (src, tgt) in enumerate(self.finals) if tgt is None and src == ev[1]), None)
            if i is None:  # never announced (or scrolled out): an ordinary new line
                self._append(ev[1], ev[2], who)
            else:  # #1532: the pending row becomes the final in place
                self.finals[i] = (ev[1], ev[2])
                self.whos[i] = who
        elif kind == "amend":  # #1395 ("amend", replaced src, src, target[, speaker]): newest final with that src
            if self.draft is not None and self.draft[0] in (ev[1], ev[2]):
                self.draft = None  # #1784: a stale streamed partial of this line must not outlive the amend
            i = next((k for k in range(len(self.finals) - 1, -1, -1)
                      if self.finals[k][0] == ev[1] and self.finals[k][1] is not None), None)
            if i is None:  # scrolled out (or never shown): an ordinary final line
                return self._apply(("final",) + ev[2:])
            self.finals[i] = (ev[2], ev[3])
            if len(ev) > 4:
                self.whos[i] = ev[4]
        elif kind == "drop":  # #1532: a heard line that will get no final (echo of our own voice): remove its row
            stale = self.draft is not None and self.draft[0] == ev[1]
            if stale:
                self.draft = None  # #1784: the dropped line's streamed partial goes away too
            i = next((k for k, (src, tgt) in enumerate(self.finals) if tgt is None and src == ev[1]), None)
            if i is None:
                return stale
            del self.finals[i], self.whos[i]
            self.partial.pop(ev[1], None)
        elif kind == "final_side":  # #1001: a speak-leg line next to a listen session: the draft row stays
            self._append(ev[1], ev[2], None)
        elif kind == "clear":
            if self.draft is None:
                return False
            self.draft = None
        else:
            return False
        return True

    def lines(self) -> list[tuple[str, bool]]:
        """[(target_text, is_draft)] oldest first, at most max_lines; a pending row is (source + mark, True)."""
        rows = [((self.partial.get(src) or src) + PENDING_MARK, True) if tgt is None else (tgt, False)
                for src, tgt in self.finals]
        if self.draft:
            rows.append((self.draft[1], True))
        return rows[-self.max_lines:]

    def history_who(self) -> list[str | None]:
        """0.3.8: speaker token per history() row (None for the draft and for lines without one)."""
        return list(self.whos) + ([None] if self.draft else [])

    def source(self) -> str:
        """Source text of the newest shown line."""
        if self.draft:
            return self.draft[0]
        return self.finals[-1][0] if self.finals else ""

    def history(self) -> list[tuple[str, str, bool]]:
        """[(src, target_text, is_draft)] for the scroll-back: every kept line, then the draft (if any).
        A pending row (#1532) is ("", source + mark, True): its text is the source, so no second source line."""
        rows = [((src, self.partial[src] + PENDING_MARK, True) if src in self.partial else ("", src + PENDING_MARK, True))
                if tgt is None else (src, tgt, False) for src, tgt in self.finals]
        if self.draft:
            rows.append((self.draft[0], self.draft[1], True))
        return rows

    def last_text(self) -> str:
        rows = self.lines()
        return rows[-1][0] if rows else ""


_CLI_FLAG = re.compile(r"(?<![\w-])--[a-z]")


def gui_error_text(e: BaseException) -> str:
    """#885: what the GUI shows for an error that stopped a run: never CLI flags, never an English-only sentence in
    a non-English UI. A localized error (`.user`, e.g. voice.VoiceUnavailable) is shown as is; a plain RuntimeError
    line only when it has no `--flag` and suits the UI language; anything else becomes one generic localized line
    (the raw text still goes to stderr for the log)."""
    from ..i18n import t, ui_lang

    user = getattr(e, "user", None)
    if isinstance(user, str) and user:
        return user
    msg = str(e).strip() if isinstance(e, RuntimeError) else ""
    if msg and not _CLI_FLAG.search(msg) and (ui_lang() == "en" or not msg.isascii()):
        return msg
    return t("gui_error_generic")


# #1020: the run_session status lines (cli.LoadProgress / announce_ready, English for the CLI) in the UI language
LOAD_STAGE_KEYS = {"speech model": "gui_stage_speech", "translation engine": "gui_stage_translation",
                   "speaker model": "gui_stage_speaker", "voice": "gui_stage_voice"}
_LOADING_RE = re.compile(r"^loading (.+?)\.\.\. \((\d+(?:\.\d+)?) s\)$")
_LOADED_RE = re.compile(r"^listening(?: - loaded in (\d+(?:\.\d+)?) s(?: \((.*)\))?)?$")
_PART_RE = re.compile(r"^(.+) (\d+(?:\.\d+)?) s$")


def _stage_name(label: str) -> str:
    from ..i18n import t

    key = LOAD_STAGE_KEYS.get(label)
    return t(key) if key else label


def load_status_text(msg: str) -> str:
    """#1020: "loading speech model... (0.0 s)" / "listening - loaded in 4.9 s (speech model 3.5 s, ...)" from the
    session -> the same line in the UI language. Any other text is returned unchanged."""
    from ..i18n import t

    m = _LOADING_RE.match(msg)
    if m:
        return t("gui_st_loading", name=_stage_name(m.group(1)), s=m.group(2))
    m = _LOADED_RE.match(msg)
    if not m:
        return msg
    if m.group(1) is None:
        return t("gui_st_listening")
    parts = []
    for p in (m.group(2) or "").split(", ") if m.group(2) else []:
        pm = _PART_RE.match(p)
        parts.append(t("gui_st_part", name=_stage_name(pm.group(1)), s=pm.group(2)) if pm else p)
    text = t("gui_st_loaded", s=m.group(1))
    return text + (t("gui_st_parts", parts=t("gui_st_sep").join(parts)) if parts else "")


SLOW_LOAD_SECS = 10.0  # above this the main window shows one friendly line instead of the breakdown


def slow_load_line(elapsed: float) -> str:
    """The friendly line shown WHILE the models are still loading once SLOW_LOAD_SECS have passed (Tk after() timer
    started at Start); "" before that. Never shown after ready."""
    from ..i18n import t

    return t("gui_load_slow") if elapsed >= SLOW_LOAD_SECS else ""


# 0.3.8: speaker avatars. Colorblind-friendly (Okabe-Ito based) hues that read on the dark navy chat background.
SPK_PALETTE = ("#E69F00", "#56B4E9", "#2EC4A0", "#F0E442", "#CC79A7", "#FF8C69", "#9BB7FF", "#B8E986")
SPK_AVATAR_FG = "#0B1220"  # dark text on the colored badge


class SpeakerColors:
    """Speaker label -> palette color, assigned in first-seen order and kept for the session (a rename never changes it;
    more speakers than colors reuse the palette from the start)."""

    def __init__(self, palette=SPK_PALETTE):
        self.palette = tuple(palette)
        self._idx: dict[str, int] = {}

    def color(self, raw: str) -> str:
        if raw not in self._idx:
            self._idx[raw] = len(self._idx)
        return self.palette[self._idx[raw] % len(self.palette)]


def speaker_prefix(book, colors, raw, fmt):
    """Floating-subtitle prefix of a line: (text, color) like ("\u25cf Speaker 2: ", a hex colour), or None without a
    resolved speaker. Same name lookup (speaker_text: renames, "Speaker N" in the UI language) and same palette color
    as the main-window avatar, so the bar needs no extra row: the prefix sits in front of the line's own text."""
    shown = speaker_text(book, raw, fmt) if raw else None
    if not shown:
        return None
    sep = "\uff1a" if any(ord(c) > 0x2e80 for c in shown) else ": "
    return f"\u25cf {shown}{sep}", colors.color(raw)


def speaker_initial(book, raw: str) -> str:
    """The avatar text: the speaker number for the default "Speaker N" label, else the first character of the name."""
    from ..speakers import LABEL_PREFIX

    shown = book.display(raw) if raw else None
    if not shown:
        return ""
    if shown.startswith(LABEL_PREFIX) and shown[len(LABEL_PREFIX):].isdigit():
        return shown[len(LABEL_PREFIX):]
    return shown[:1].upper()


def default_listen_cmd(mdir=None, bundled: bool | None = None) -> list[str]:
    """#1358: the child that downloads the missing default listen models (`--default-listen`, resumable)."""
    cmd = download_cmd("small", mdir, bundled)
    i = cmd.index("--whisper")
    return [*cmd[:i], "--default-listen", *cmd[i + 2:]]
