"""gui.json settings: defaults, load and save (#1664: moved down from gui/core.py).

Flow modules (selftest, speedprobe) read these without importing the GUI package. gui/core.py re-exports every
name here, so `tinglongwu.gui.core.load_settings` / `.settings_path` / `.DEFAULTS` keep working."""
from __future__ import annotations

import json
import os
from pathlib import Path

from . import diag as _diag

DEFAULTS = {
    "lang": "auto", "target": "zh-tw", "engine": "auto", "model": "small", "glossary": "",
    "save": False, "save_path": "", "show_src": False, "font_size": 28, "live_draft": True,
    "x": None, "y": None, "panel_w": None, "panel_h": None, "panel_x": None, "panel_y": None,  # #939
    "source": "system", "preset": "meeting", "overlay_lines": 3, "theme": "dark",
    "floating_bar": False, "log_font_size": 14, "font_family": "", "quality": "auto",
    "advanced_open": False,  # #399: the engine dropdown lives under a collapsed Advanced section
    "listen_device": "",  # #1070: "" = listen to every output (not our cable); else one output name (Advanced)
    "mic_device": "",  # #894: "" = the system default microphone; else one microphone name (Advanced)
    "voice": False, "voice_device": "",  # #330: speak each translated line on a separate output (off by default)
    "overlay_alpha": 0.82,  # #333: subtitle bar opacity (right-click > Opacity), kept across restarts
    "gpu_pack_never_ask": False,  # #1525: "Don't ask again" on the GPU speed-up pack banner (gpu_offer.DISMISS_KEY)
    "gpu_pack_auto_off": False,  # #1736: the user cancelled the automatic GPU pack download (gpu_offer.AUTO_OFF_KEY)
    "vulkan_offer_never_ask": False,  # #1775: "Don't ask again" on the Vulkan pack banner (vulkan_offer.DISMISS_KEY)
    "tour_seen": False,  # #314: the first-launch "3 steps" overlay was closed with "Got it"
    "usage_asked": False,  # #680: the one-time "what do you mainly use it for?" question was answered or skipped
    "usage_use": "",  # #680: the answer (one of USAGE_CHOICES, "" = none); local only, never sent anywhere
    "scene": "video",  # 0.3.8: the 情境 row: "meeting" (listen + speak) or "video" (listen only); last choice kept
    "speaker_id": False,  # 0.3.8: 標示說話者; off = sherpa-onnx and the speaker model are never loaded
    "notes_float": False,  # #1705: meeting notes popped out into their own floating window (False = side panel)
    "notes_x": None, "notes_y": None, "notes_w": None, "notes_h": None,  # #1705: that window's place and size
    "topics": None,  # #611: topic notes side panel; None = default (on with CUDA, off on CPU-only), True/False = chosen
    "topics_model": "",  # #611: local GGUF for --topics; "" = the downloaded #1378 default (TOPICS_KEY), if installed
    "gpu_use": "auto",  # #1531: Adjust > Advanced "Use GPU": auto (GPU when it works, else CPU) / off (always CPU)
    "gpu_fallback": "",  # #1531: reason the first-start self-test fell back to CPU ("" = none); written by selftest.apply_to_gui
    "send_mode": "auto",  # #834/#1052: how a speak line is sent (auto = straight away, confirm = show me first); owner default: auto
}
SEND_MODES = ("confirm", "countdown", "auto")  # same choices as reverse.sendmode.MODES (the CLI keeps all three)
ENGINES = ["auto", "hymt", "hymt7b"]  # #276, #471: Hy-MT2 only (OPUS removed); #1351: the opt-in 7B (GPU, never auto)
# auto = what hwprobe recommends for this PC; fast / balanced / quality trade speed for accuracy on the same
# hardware; manual = the Engine + Speech model boxes decide (#81)
QUALITIES = ["auto", "fast", "balanced", "quality", "manual"]
FONT_MIN, FONT_MAX = 12, 72
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


def settings_path() -> Path:
    base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    return Path(base) / "tinglongwu" / "gui.json"


def _gpu_usable() -> bool:
    from .device import cuda_device_count

    return cuda_device_count() > 0


def load_settings(path=None, gpu=_gpu_usable) -> dict:
    """Saved settings merged over DEFAULTS; wrong-typed or unreadable values fall back to the default.
    First run (no saved file) without a usable GPU: live draft starts off, it doubles the work on a CPU."""
    out = dict(DEFAULTS)
    fresh = True
    try:
        from .i18n import _system_tag

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
    if not isinstance(out.get("font_family"), str):
        out["font_family"] = ""
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
