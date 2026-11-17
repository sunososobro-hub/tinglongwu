"""GUI logic without any tkinter: event queue sink, subtitle state, settings persistence.

Worker threads only ever put tuples on a queue.Queue; the tkinter main thread polls it with after().
Events: ("draft", src_text, translation) | ("final", src_text, target_text) | ("clear",) |
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
from ..langs import SOURCE_LANGS
from ..stream import DraftRenderer

DEFAULTS = {
    "lang": "auto", "target": "zh-tw", "engine": "auto", "model": "small", "glossary": "",
    "save": False, "save_path": "", "show_src": False, "font_size": 28, "live_draft": True,
    "x": None, "y": None, "panel_w": None, "panel_h": None, "panel_x": None, "panel_y": None,  # #939
    "source": "system", "preset": "meeting", "overlay_lines": 3, "theme": "dark",
    "floating_bar": False, "log_font_size": 14, "quality": "auto",
    "advanced_open": False,  # #399: the engine dropdown lives under a collapsed Advanced section
    "listen_device": "",  # #1070: "" = listen to every output (not our cable); else one output name (Advanced)
    "mic_device": "",  # #894: "" = the system default microphone; else one microphone name (Advanced)
    "voice": False, "voice_device": "",  # #330: speak each translated line on a separate output (off by default)
    "overlay_alpha": 0.82,  # #333: subtitle bar opacity (right-click > Opacity), kept across restarts
    "tour_seen": False,  # #314: the first-launch "3 steps" overlay was closed with "Got it"
    "usage_asked": False,  # #680: the one-time "what do you mainly use it for?" question was answered or skipped
    "usage_use": "",  # #680: the answer (one of USAGE_CHOICES, "" = none); local only, never sent anywhere
    "scene": "video",  # 0.3.8: the 情境 row: "meeting" (listen + speak) or "video" (listen only); last choice kept
    "speaker_id": False,  # 0.3.8: 標示說話者; off = sherpa-onnx and the speaker model are never loaded
    "topics": None,  # #611: topic notes side panel; None = default (on with CUDA, off on CPU-only), True/False = chosen
    "topics_model": "",  # #611: local GGUF for --topics; "" = the downloaded #1378 default (TOPICS_KEY), if installed
    "send_mode": "auto",  # #834/#1052: how a speak line is sent (auto = straight away, confirm = show me first); owner default: auto
}
SEND_MODES = ("confirm", "countdown", "auto")  # same choices as reverse.sendmode.MODES (the CLI keeps all three)
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
                       "voice_device", "send_mode", "listen_device", "mic_device")


def restart_needed(at_start: dict, now: dict) -> list[str]:
    """#983: the start-time settings whose value in `now` differs from what the running pipeline started with."""
    return [k for k in START_TIME_SETTINGS if k in at_start and k in now and at_start[k] != now[k]]


LANGS = SOURCE_LANGS  # auto, focus ja/en/ru, zh, then the experimental ones (#58)
TARGETS = ["zh-tw", "zh-cn", "en", "ja", "ko", "es", "fr", "de", "th", "vi", "id"]  # #1163: Hy-MT2 direct targets
ENGINES = ["auto", "hymt", "hymt7b"]  # #276, #471: Hy-MT2 only (OPUS removed); #1351: the opt-in 7B (GPU, never auto)
# auto = what hwprobe recommends for this PC; fast / balanced / quality trade speed for accuracy on the same
# hardware; manual = the Engine + Speech model boxes decide (#81)
QUALITIES = ["auto", "fast", "balanced", "quality", "manual"]
PRESETS = ["meeting", "chat"]  # meeting = whole sentences (~5-8 s latency); chat = lower latency, more fragments
SOURCES = ["system", "mic"]  # system = whatever the PC is playing (loopback); mic = microphone
MODELS = ["tiny", "base", "small", "medium", "large-v3", "large-v3-turbo"]
FONT_MIN, FONT_MAX = 12, 72
FONT_PRESETS = {"S": 20, "M": 28, "L": 36, "XL": 48}  # subtitle size picker in the control window
HISTORY_LINES = 200  # finals kept in the subtitle bar's scroll-back
ROWS_MIN, ROWS_MAX = 1, 5  # visible text rows of the subtitle bar (drag its top edge to change)
ALPHA_MIN, ALPHA_MAX, ALPHA_STEP = 0.3, 1.0, 0.1  # #333: below 0.3 the subtitles are hard to read on a busy screen


def target_for_locale(tag: str | None) -> str:
    """OS locale tag -> default 'Translate to' value: zh-CN/SG -> zh-cn, other zh (and unknown/empty) -> zh-tw, else en."""
    t = (tag or "").strip().lower().replace("_", "-").split(".")[0]
    if not t or t in ("c", "posix"):
        return DEFAULTS["target"]
    if t.startswith("zh"):
        return "zh-cn" if ("hans" in t or t.endswith(("-cn", "-sg"))) and "hant" not in t else "zh-tw"
    return "en"


QUICK_MODES = ("listen", "speak")  # #294 first-screen buttons: "Listen to others" / "I speak"
SESSION_MODES = ("listen", "speak", "two-way")  # #981: two-way = both legs (meeting / face-to-face)


class SameLanguageError(ValueError):
    """#981: My language and Other side language are the same language: nothing to translate, no session starts.
    `key` is the i18n key of the one-line message the GUI shows instead of starting."""

    key = "gui_same_lang"


def lang_base(code: str | None) -> str:
    """Language without the script variant: zh-tw / zh-cn -> zh; anything else unchanged ("" for None)."""
    return "zh" if code in ("zh", "zh-tw", "zh-cn") else (code or "")


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


def settings_path() -> Path:
    base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    return Path(base) / "tinglongwu" / "gui.json"


def _gpu_usable() -> bool:
    from ..device import cuda_device_count

    return cuda_device_count() > 0


def load_settings(path=None, gpu=_gpu_usable) -> dict:
    """Saved settings merged over DEFAULTS; wrong-typed or unreadable values fall back to the default.
    First run (no saved file) without a usable GPU: live draft starts off, it doubles the work on a CPU."""
    out = dict(DEFAULTS)
    fresh = True
    try:
        from ..i18n import _system_tag

        out["target"] = target_for_locale(_system_tag())  # first run (no saved choice): follow the OS locale
    except Exception:  # noqa: BLE001
        _diag.swallowed(__name__)
        pass
    try:
        data = json.loads(Path(path or settings_path()).read_text("utf-8"))
        if isinstance(data, dict):
            fresh = False
            for k, dv in DEFAULTS.items():
                v = data.get(k)
                if v is None:
                    continue
                if k == "topics":  # #611: None (default) / True / False
                    if isinstance(v, bool):
                        out[k] = v
                elif dv is None:  # x / y
                    if isinstance(v, int) and not isinstance(v, bool):
                        out[k] = v
                elif isinstance(v, type(dv)) and not (type(dv) is int and isinstance(v, bool)):
                    out[k] = v
                elif type(dv) is float and isinstance(v, int) and not isinstance(v, bool):
                    out[k] = float(v)
    except Exception:  # noqa: BLE001 - missing/corrupt file: just use defaults
        _diag.swallowed(__name__)
        pass
    out["font_size"] = clamp_font(out["font_size"])
    if out["quality"] not in QUALITIES:
        out["quality"] = DEFAULTS["quality"]
    if not fresh and "quality" not in data and (out["engine"], out["model"]) != (DEFAULTS["engine"], DEFAULTS["model"]):
        out["quality"] = "manual"  # settings saved before #81 with a hand-picked engine/model: keep that choice
    if out["engine"] not in ENGINES:  # #276: a saved removed engine (an old LLM id) is no longer a CLI choice
        out["engine"] = DEFAULTS["engine"]
    if fresh:
        try:
            out["live_draft"] = bool(gpu())
        except Exception:  # noqa: BLE001
            _diag.swallowed(__name__)
            pass
    out["log_font_size"] = clamp_font(out["log_font_size"], DEFAULTS["log_font_size"])
    out["overlay_lines"] = clamp_rows(out["overlay_lines"])
    out["overlay_alpha"] = clamp_alpha(out["overlay_alpha"])
    if out["send_mode"] == "countdown":  # #1052: removed from the GUI; it behaved like sending straight away
        out["send_mode"] = "auto"
    if out["send_mode"] not in SEND_MODES:
        out["send_mode"] = DEFAULTS["send_mode"]
    return out


def save_settings(s: dict, path=None) -> bool:
    try:
        p = Path(path or settings_path())
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({k: s.get(k, DEFAULTS[k]) for k in DEFAULTS}, ensure_ascii=False, indent=2), "utf-8")
        return True
    except Exception:  # noqa: BLE001
        return False


def clamp_font(n, default=None) -> int:
    try:
        return max(FONT_MIN, min(FONT_MAX, int(n)))
    except (TypeError, ValueError):
        return DEFAULTS["font_size"] if default is None else default


def clamp_rows(n) -> int:
    try:
        return max(ROWS_MIN, min(ROWS_MAX, int(n)))
    except (TypeError, ValueError):
        return DEFAULTS["overlay_lines"]


def clamp_alpha(a) -> float:
    """Subtitle bar opacity in [ALPHA_MIN, ALPHA_MAX], 2 decimals; junk (None, NaN, text) -> the default."""
    try:
        a = float(a)
    except (TypeError, ValueError):
        return DEFAULTS["overlay_alpha"]
    if a != a:  # NaN
        return DEFAULTS["overlay_alpha"]
    return round(max(ALPHA_MIN, min(ALPHA_MAX, a)), 2)


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
    "quality": {"en": {"auto": "Auto", "fast": "Fast", "balanced": "Balanced", "quality": "Best quality", "manual": "Manual"},
                "ru": {"auto": "Авто", "fast": "Быстро", "balanced": "Баланс", "quality": "Лучшее качество", "manual": "Вручную"},
                "zh-TW": {"auto": "自動", "fast": "快速", "balanced": "平衡", "quality": "最佳品質", "manual": "手動"},
                "ja": {"auto": "自動", "fast": "高速", "balanced": "バランス", "quality": "最高品質", "manual": "手動"}},
    "source": {"en": {"system": "PC sound", "mic": "Microphone"},
               "ru": {"system": "Звук ПК", "mic": "Микрофон"},
               "zh-TW": {"system": "電腦音效", "mic": "麥克風"},
               "ja": {"system": "PC の音", "mic": "マイク"}},
    "preset": {"en": {"meeting": "Meeting (full sentences)", "chat": "Chat (faster)"},
               "ru": {"meeting": "Встреча (целые фразы)", "chat": "Чат (быстрее)"},
               "zh-TW": {"meeting": "會議（完整句子）", "chat": "聊天（較快）"},
               "ja": {"meeting": "会議（文単位）", "chat": "チャット（速め）"}},
    "engine": {"en": {"auto": "Auto"}, "ru": {"auto": "Авто"}, "zh-TW": {"auto": "自動"}, "ja": {"auto": "自動"}},
}


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
    return t("gui_mt_path_gpu", api={"vulkan": "Vulkan", "cuda": "CUDA"}.get(backend, backend))


def cpu_mode_line(device: str, mt_ms: float | None = None, lang: str | None = None,
                  path: tuple[str, int] | None = None) -> str | None:
    """#780: status text "CPU mode" (+ per-line MT ms) when running on the CPU, else None.
    path (#1198): (backend, threads) of the MT model, shown after the ms so the user sees CPU vs Vulkan."""
    from ..i18n import t

    if device != "cpu":
        return None
    base = t("gui_cpu_mode")
    if mt_ms is None:
        return base
    where = mt_path_text(path)
    return f"{base} - {t('gui_cpu_mt_ms', ms=round(mt_ms))}" + (f" ({where})" if where else "")


def drop_cpu_mode_notice(notice: str, status: str) -> str:
    """#1241: the CPU-mode text is shown once. When the status line already carries it (with the per-line MT ms),
    remove it from the orange notice (a whole line, or a " | ..." tail); other notice text stays."""
    from ..i18n import t

    cpu = t("gui_cpu_mode")
    if not notice or cpu not in (status or ""):
        return notice
    keep = []
    for ln in notice.split(chr(10)):
        parts = [p for p in ln.split(" | ") if p.strip() != cpu]
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

    def write(self, r: Result) -> None:
        from ..reverse.echoguard import shared

        if shared().check(r.original, r.target if r.src != r.tgt else ""):  # #1070: our own voice coming back
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

    heads = {m["listen_silent"].split("{names}")[0] for m in MESSAGES.values() if "listen_silent" in m}
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


class SubtitleState:
    """What the overlay shows: last `max_lines` lines; a draft is replaced in place by the final."""

    def __init__(self, max_lines: int = 2):
        self.max_lines = max_lines
        self.finals: list[tuple[str, str]] = []  # (src, target), oldest first
        self.draft: tuple[str, str] | None = None
        self.whos: list[str | None] = []  # 0.3.8: speaker token of each final (aligned with finals; None = none)
        self.pending: list[str] = []  # #53: sources heard, translation still running (newest one is the draft)
        self.paused = False  # frozen display: events still update the state (nothing is lost) but nothing is redrawn

    def apply(self, ev: tuple) -> bool:
        """Apply an event; True if the display should be redrawn."""
        if self._apply(ev):
            return not self.paused
        return False

    def _apply(self, ev: tuple) -> bool:
        kind = ev[0]
        if kind == "draft":
            self.draft = (ev[1], ev[2])
        elif kind == "source":
            self.pending.append(ev[1])
            self.draft = ("", ev[1])  # same shape as a source-only live draft: the source text, dim
        elif kind == "final":
            if self.pending and self.pending[0] == ev[1]:
                self.pending.pop(0)
            self.draft = ("", self.pending[-1]) if self.pending else None
            self.finals.append((ev[1], ev[2]))
            self.whos.append(ev[3] if len(ev) > 3 else None)
            del self.finals[:-self.max_lines], self.whos[:-self.max_lines]
        elif kind == "amend":  # #1395 ("amend", replaced src, src, target[, speaker]): newest final with that src
            i = next((k for k in range(len(self.finals) - 1, -1, -1) if self.finals[k][0] == ev[1]), None)
            if i is None:  # scrolled out (or never shown): an ordinary final line
                return self._apply(("final",) + ev[2:])
            self.finals[i] = (ev[2], ev[3])
            if len(ev) > 4:
                self.whos[i] = ev[4]
        elif kind == "final_side":  # #1001: a speak-leg line next to a listen session: the draft row stays
            self.finals.append((ev[1], ev[2]))
            self.whos.append(None)
            del self.finals[:-self.max_lines], self.whos[:-self.max_lines]
        elif kind == "clear":
            if self.draft is None:
                return False
            self.draft = None
        else:
            return False
        return True

    def lines(self) -> list[tuple[str, bool]]:
        """[(target_text, is_draft)] oldest first, at most max_lines."""
        rows = [(t, False) for _, t in self.finals]
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
        """[(src, target_text, is_draft)] for the scroll-back: every kept final, then the draft (if any)."""
        rows = [(src, t, False) for src, t in self.finals]
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
    """Floating-subtitle prefix of a line: (text, color) like ("\u25cf Speaker 2: ", "#e6a23c"), or None without a
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
