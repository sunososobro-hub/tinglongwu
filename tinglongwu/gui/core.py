"""GUI logic without any tkinter: event queue sink, subtitle state, settings persistence.

Worker threads only ever put tuples on a queue.Queue; the tkinter main thread polls it with after().
Events: ("draft", src_text, translation) | ("final", src_text, target_text) | ("clear",) |
        ("status", text) | ("notice", text) | ("error", text) | ("stopped",)
"""
from __future__ import annotations

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
    "x": None, "y": None, "source": "system", "preset": "meeting", "overlay_lines": 3, "theme": "dark",
    "floating_bar": False, "log_font_size": 14, "quality": "auto",
    "advanced_open": False,  # #399: the engine dropdown lives under a collapsed Advanced section
    "voice": False, "voice_device": "",  # #330: speak each translated line on a separate output (off by default)
    "overlay_alpha": 0.82,  # #333: subtitle bar opacity (right-click > Opacity), kept across restarts
    "tour_seen": False,  # #314: the first-launch "3 steps" overlay was closed with "Got it"
    "send_mode": "confirm",  # #834: how a speak line is sent (confirm / countdown / auto); owner default: confirm
}
SEND_MODES = ("confirm", "countdown", "auto")  # same choices as reverse.sendmode.MODES


def send_mode_notice_key(mode: str) -> str | None:
    """#834: i18n key of the one-line quality notice shown when a direct-send mode is chosen, else None."""
    return "gui_send_mode_notice" if mode in ("countdown", "auto") else None
LANGS = SOURCE_LANGS  # auto, focus ja/en/ru, zh, then the experimental ones (#58)
TARGETS = ["zh-tw", "zh-cn", "en"]
ENGINES = ["auto", "hymt"]  # #276, #471: Hy-MT2 is the only engine offered (OPUS removed)
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


def quick_settings(s: dict, mode: str) -> dict:
    """#294 easy start: session settings for a first-screen button; `s` (the saved settings) is not changed.
    The user only picks their own language (`target`, remembered). listen = PC sound (loopback) -> my language,
    source language as set (auto by default). speak = microphone in my language -> the other language
    (English for a Chinese speaker, Traditional Chinese for an English speaker). Engine/model stay on `quality`."""
    if mode not in QUICK_MODES:
        raise ValueError(f"unknown quick mode: {mode!r}")
    out = dict(s)
    mine = s.get("target") if s.get("target") in TARGETS else DEFAULTS["target"]
    if mode == "listen":
        out.update(source="system", target=mine)
    else:
        zh = mine.startswith("zh")
        out.update(source="mic", lang="zh" if zh else "en", target="en" if zh else "zh-tw")
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
                if dv is None:  # x / y
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


_MARK_OK, _MARK_MISSING = "\u2713 ", " (\u9700\u4e0b\u8f09 / not downloaded)"
_MARK_HEAVY = " (too heavy for this PC)"  # #780: medium / large-v3 on a CPU-mode PC with < 24 GB RAM


def cpu_ram_gb(has_cuda, ram=None) -> float | None:
    """#780: RAM in GB for the model picker's "too heavy" marks; None (no marks) when a CUDA device is present."""
    from ..hwprobe import ram_mib

    if has_cuda:
        return None
    mib = (ram or ram_mib)()
    return None if mib is None else mib / 1024


def cpu_mode_line(device: str, mt_ms: float | None = None, lang: str | None = None) -> str | None:
    """#780: status text "CPU mode" (+ per-line MT ms) when running on the CPU, else None."""
    from ..i18n import t

    if device != "cpu":
        return None
    base = t("gui_cpu_mode")
    return base if mt_ms is None else f"{base} - {t('gui_cpu_mt_ms', ms=round(mt_ms))}"


def model_labels(mdir=None, ram_gb: float | None = None) -> list[str]:
    """Dropdown entries: downloaded Whisper models first, marked with a check; the rest marked as needing download (#96).
    ram_gb (CPU mode, #780): heavy models get a "too heavy for this PC" mark when RAM is below 24 GB."""
    from ..hwprobe import too_heavy
    from ..models import whisper_installed

    have = whisper_installed(mdir)
    heavy = lambda m: _MARK_HEAVY if too_heavy(m, ram_gb) else ""  # noqa: E731
    return ([_MARK_OK + m + heavy(m) for m in MODELS if m in have]
            + [m + _MARK_MISSING + heavy(m) for m in MODELS if m not in have])


_ENGINE_DIR = {"hymt": "hymt2-1.8b"}  # models/<dir> per engine (auto needs no model of its own); #471: no opus
_ENGINE_HINT = {"hymt": "tlwu-setup --hymt"}


def engine_labels(mdir=None) -> list[str]:
    """#399: Advanced engine entries; an engine whose model is not installed carries the setup command to get it."""
    from ..models import models_dir

    d = models_dir(str(mdir) if mdir else None)
    return [e if e == "auto" or (d / _ENGINE_DIR[e]).is_dir() else f"{e} ({_ENGINE_HINT[e]})" for e in ENGINES]


def engine_from_label(label: str) -> str:
    """The plain engine id from an engine_labels() entry (also accepts a plain id)."""
    return (label or "").split(" (", 1)[0].strip()


def model_from_label(label: str) -> str:
    """The plain model name from a dropdown entry (also accepts a plain name)."""
    return label.replace(_MARK_OK, "", 1).replace(_MARK_HEAVY, "").replace(_MARK_MISSING, "").strip()


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
        bundled = bool(getattr(sys, "frozen", False)) or os.environ.get("TLWU_BUNDLED") == "1"
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
                put(("dl_line", parts[-1][:90]))
        rc = p.wait()
    except Exception as e:  # noqa: BLE001
        put(("dl_line", f"{type(e).__name__}: {e}"[:90]))
        rc = -1
    put(("dl_done", model, rc))
    return rc


def run_pack_download(pack: str, put, mdir=None, popen=None, bundled: bool | None = None) -> int:
    """Worker thread: run the pack download child; `put` gets ("pack_line", text) per line and ("pack_done", pack, rc)."""
    import subprocess

    popen = popen or subprocess.Popen
    try:
        p = popen(pack_download_cmd(pack, mdir, bundled), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                  env=download_env(), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        for raw in p.stdout:
            line = (raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw).replace("\r", "\n")
            parts = [x.strip() for x in line.split("\n") if x.strip()]
            if parts:
                put(("pack_line", parts[-1][:90]))
        rc = p.wait()
    except Exception as e:  # noqa: BLE001
        put(("pack_line", f"{type(e).__name__}: {e}"[:90]))
        rc = -1
    put(("pack_done", pack, rc))
    return rc


def settings_to_argv(s: dict, outputs: list[str] | None = None, notice=None) -> list[str]:
    """Map GUI settings to CLI args (so the GUI reuses the CLI's parser and defaults).
    outputs: the current voice_outputs() (#448): a saved voice_device that is no longer listed is left out (default
    output) and notice(str) gets one line; None or [] (list unknown / no audio stack) passes the saved name on."""
    to, variant = {"zh-tw": ("zh", "tw"), "zh-cn": ("zh", "cn"), "en": ("en", "tw")}.get(s.get("target"), ("zh", "tw"))
    argv = ["--lang", s.get("lang") or "auto", "--to", to, "--zh-variant", variant,
            "--preset", s.get("preset") if s.get("preset") in PRESETS else "meeting"]
    quality = s.get("quality") if s.get("quality") in QUALITIES else DEFAULTS["quality"]
    if quality == "manual":
        engine = s.get("engine") if s.get("engine") in ENGINES else "auto"  # #471: a removed engine (opus) -> auto
        argv += ["--engine", engine, "--model", usable_model(s.get("model"))]
    else:  # the CLI picks engine + speech model from the hardware probe
        argv += ["--quality", quality]
    if s.get("source") == "mic":
        argv += ["--mic"]
    if s.get("glossary"):
        argv += ["--glossary", s["glossary"]]
    if s.get("save"):
        argv += ["--save"] + ([s["save_path"]] if s.get("save_path") else [])
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


class QueueSink(Sink):
    """Final lines -> queue (runs on the worker thread; never touches tkinter)."""

    def __init__(self, q: queue.Queue):
        self.q = q

    def write_source(self, src: str, original: str, speaker: str | None = None) -> None:
        self.q.put(("source", original))  # shown dim until its "final" arrives (#53)

    def write(self, r: Result) -> None:
        self.q.put(("final", r.original, r.target if r.src != r.tgt and r.target else r.original))


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


class SubtitleState:
    """What the overlay shows: last `max_lines` lines; a draft is replaced in place by the final."""

    def __init__(self, max_lines: int = 2):
        self.max_lines = max_lines
        self.finals: list[tuple[str, str]] = []  # (src, target), oldest first
        self.draft: tuple[str, str] | None = None
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
            del self.finals[:-self.max_lines]
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
