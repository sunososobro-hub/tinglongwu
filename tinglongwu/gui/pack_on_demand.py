"""#673: offer a missing language pack at the moment it is needed. No tkinter here (the GUI passes `ask`/`notify`).

The download itself runs in a tlwu-setup child (`--packs LANG`), resumable and SHA256-checked (tinglongwu_setup.resume);
this module only decides whether to ask, words the question, and reports the result."""
from __future__ import annotations

import json
from pathlib import Path

DEFAULT_MBPS = 10.0  # fallback download speed (MB/s) when none was measured
PACK_OF = {"zh": "zh", "en": "en", "ja": "ja", "ru": "ru"}  # source language code -> pack name
TEXT = {
    "en": "This needs the {pack} pack (about {mb} MB, about {min} min). Needs internet to download once, "
          "works offline afterwards.",
    "ru": "Нужен пакет {pack} (около {mb} МБ, около {min} мин). Для однократной загрузки нужен интернет, "
          "потом работает офлайн.",
    "zh-TW": "這需要「{pack}」語言包（約 {mb} MB，約 {min} 分鐘）。只需連網下載一次，之後可離線使用。",
}
BUTTONS = {"en": ("Download", "Not now"), "ru": ("Скачать", "Не сейчас"), "zh-TW": ("下載", "稍後")}


def est_minutes(mb: float, mbps: float | None = None) -> int:
    """Whole minutes (at least 1) to download `mb` MB at `mbps` MB/s (unknown/invalid -> DEFAULT_MBPS)."""
    speed = mbps if mbps and mbps > 0 else DEFAULT_MBPS
    return max(1, round(mb / speed / 60))


def dialog_text(ui_lang: str, pack: str, mb: float, mbps: float | None = None) -> str:
    tag = ui_lang if ui_lang in TEXT else "en"
    return TEXT[tag].format(pack=pack, mb=round(mb), min=est_minutes(mb, mbps))


def needed_packs(source_lang: str, target_lang: str) -> list[str]:
    """Packs a (source, target) pair needs; 'auto' and unknown codes need none. zh-tw / zh-cn -> zh."""
    out = []
    for code in (source_lang, target_lang):
        pack = PACK_OF.get((code or "").lower().split("-")[0])
        if pack and pack not in out:
            out.append(pack)
    return out


# keep in sync with tinglongwu_setup.packs (the runtime package must not import the network-capable setup package;
# tests/test_gui_pack_on_demand.py compares the two tables)
PIPER_RU = "piper-ru_RU-dmitri-medium"
PACKS = {"zh": [], "en": [], "ja": ["hymt2-1.8b"], "ko": ["hymt2-1.8b"], "ru": ["hymt2-1.8b", PIPER_RU]}
SIZE_MB = {"hymt2-1.8b": 1100, PIPER_RU: 60}
OPUS_MB = 100


def _pack_mb(pack: str) -> int:
    return sum(SIZE_MB.get(k, OPUS_MB) for k in PACKS[pack])


def _missing(pack: str, mdir: Path) -> list[str]:
    """Model keys of `pack` not installed: a key counts only if the local manifest.json pinned it and its dir exists."""
    mf = Path(mdir) / "manifest.json"
    man = json.loads(mf.read_text("utf-8")) if mf.exists() else {}
    return [k for k in PACKS[pack]
            if not (k in man and (Path(mdir) / ("opus/" + k[5:] if k.startswith("opus-") else k)).is_dir())]


def _defaults(mdir: Path, missing, size_mb):
    return missing or (lambda p: _missing(p, mdir)), size_mb or _pack_mb


def first_missing(source_lang: str, target_lang: str, mdir: Path, missing=None, size_mb=None) -> tuple[str, int] | None:
    """(pack, size in MB) of the first pack the pair needs that is not installed, or None. `missing(pack)` /
    `size_mb(pack)` default to the pack table + the local models/manifest.json."""
    missing, size_mb = _defaults(mdir, missing, size_mb)
    for pack in needed_packs(source_lang, target_lang):
        if missing(pack):
            return pack, size_mb(pack)
    return None


def ensure_packs(source_lang: str, target_lang: str, mdir: Path, ask, download, ui_lang: str = "en",
                 mbps: float | None = None, missing=None, size_mb=None) -> bool:
    """True when every pack the pair needs is installed (after offering the download); False after "Not now" or a
    failed download, so the caller keeps the previous mode/language. `ask(text, ok_label, no_label) -> bool`;
    `download(pack) -> returncode`."""
    missing, size_mb = _defaults(mdir, missing, size_mb)
    while (need := first_missing(source_lang, target_lang, mdir, missing, size_mb)):
        pack, mb = need
        yes, no = BUTTONS.get(ui_lang, BUTTONS["en"])
        if not ask(dialog_text(ui_lang, pack, mb, mbps), yes, no):
            return False
        if download(pack) != 0 or missing(pack):
            return False
    return True
