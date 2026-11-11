"""Language packs (#178): an always-installed core (app, Python deps, one Whisper model, netguard) plus one pack per
language, chosen at install time (default zh+en) and fetched later only when that language is picked.

  tlwu-setup --packs zh,en          download these packs (network; SHA256-pinned like every other model)
  tlwu-setup --list                 offline: which packs are installed / missing (+ approximate size)
  tlwu-setup --pack-prompt ja       offline check; if ja is missing ask "download now? [y/N]" (default no)
  tlwu-setup --export-pack ja X.zip pack an installed language for an offline machine
  tlwu-setup --import-pack X.zip    offline: install such a zip (SHA256 vs its PACK.json and the committed pins)

Downloads only ever run here, in tlwu-setup (its own process), never inside the netguarded runtime. Kokoro (en/ja
voice) is one shared model file fetched by the installers, so it is not split per language. The Vosk Russian voice
stays opt-in (no upstream license) and is never part of a pack.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

PIPER_RU_DEFAULT = "piper-ru_RU-dmitri-medium"  # keep in sync with downloader.piper_ru_dir("dmitri")
# language -> model keys (keys of models/manifest.json). zh is the hub language; OPUS pivots through en.
PACKS: dict[str, list[str]] = {
    "zh": ["opus-en-zh", "opus-zh-en"],
    "en": ["opus-en-zh", "opus-zh-en"],
    "ja": ["hymt2-1.8b", "opus-ja-en"],
    "ru": ["opus-ru-en", "opus-en-ru", PIPER_RU_DEFAULT],
}
DEFAULT_PACKS = ("zh", "en")
# approximate download size per model in MB, only for the prompt: Argos OPUS packages are ~100 MB each, the Hy-MT2
# GGUF ~1.1 GB and a Piper medium voice ~60 MB (sizes as stated in downloader.py's help texts).
SIZE_MB = {"hymt2-1.8b": 1100, PIPER_RU_DEFAULT: 60}
OPUS_MB = 100
PROMPT = {  # setup does not import tinglongwu, so the three UI languages live here (like downloader.VOSK_LICENSE_NOTE)
    "en": "pack {lang} (~{mb} MB) missing — download now? [y/N] ",
    "ru": "пакет {lang} (~{mb} МБ) не установлен — скачать сейчас? [y/N] ",
    "zh-TW": "語言包 {lang}（約 {mb} MB）尚未安裝 — 現在下載嗎？[y/N] ",
}


def model_dir(mdir: Path, key: str) -> Path:
    return mdir / ("opus/" + key[5:] if key.startswith("opus-") else key)


def _manifest(mdir: Path) -> dict:
    mf = mdir / "manifest.json"
    return json.loads(mf.read_text("utf-8")) if mf.exists() else {}


def _langs(spec: str) -> list[str]:
    langs = [x.strip() for x in spec.split(",") if x.strip()]
    bad = [x for x in langs if x not in PACKS]
    if bad:
        sys.exit(f"unknown language pack(s): {bad}; available: {', '.join(PACKS)}")
    return langs


def pack_size_mb(lang: str) -> int:
    return sum(SIZE_MB.get(k, OPUS_MB) for k in PACKS[lang])


def missing(lang: str, mdir: Path) -> list[str]:
    """Model keys of `lang` not on disk (a key counts only if its dir exists AND the local manifest pinned it)."""
    man = _manifest(mdir)
    return [k for k in PACKS[lang] if not (k in man and model_dir(mdir, k).is_dir())]


def list_packs(mdir: Path) -> int:
    for lang in PACKS:
        miss = missing(lang, mdir)
        print(f"{lang}  installed" if not miss else f"{lang}  missing (~{pack_size_mb(lang)} MB): {', '.join(miss)}")
    return 0


def keys_for(langs: str) -> list[str]:
    """Model keys of the given packs, deduplicated (zh and en share the en<->zh pairs)."""
    return list(dict.fromkeys(k for lang in _langs(langs) for k in PACKS[lang]))


def install(langs: str, mdir: Path) -> int:
    """Download the given packs (network): downloader --packs reuses the pinned getters, so the same SHA256 checks
    apply as for every other model."""
    from . import downloader

    return downloader.main(["--packs", langs, "--models-dir", str(mdir)])


def _ui_lang() -> str:
    lang = (os.environ.get("TLWU_UI_LANG") or "en").strip().lower()
    return "zh-TW" if lang.startswith("zh") else ("ru" if lang.startswith("ru") else "en")


def prompt(lang: str, mdir: Path, ask=None) -> int:
    """0 if `lang` is installed (or was just downloaded after a yes); 1 if missing and declined (default = no)."""
    _langs(lang)
    if not missing(lang, mdir):
        return 0
    try:
        ans = (ask or input)(PROMPT[_ui_lang()].format(lang=lang, mb=pack_size_mb(lang)))
    except EOFError:
        ans = ""
    if ans.strip().lower() not in ("y", "yes"):
        return 1
    rc = install(lang, mdir)
    return rc or (1 if missing(lang, mdir) else 0)


def export(lang: str, out: Path, mdir: Path) -> int:
    _langs(lang)
    miss = missing(lang, mdir)
    if miss:
        sys.exit(f"pack {lang} is not fully installed here (missing {miss}); run tlwu-setup --packs {lang} first")
    man = _manifest(mdir)
    meta = {"lang": lang, "models": {k: man[k] for k in PACKS[lang]}}
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("PACK.json", json.dumps(meta, indent=1))
        for k in PACKS[lang]:
            for rel in man[k]["files"]:
                z.write(model_dir(mdir, k) / rel, f"models/{k}/{rel}")
    print(f"pack {lang} written: {out}")
    return 0


def import_pack(zpath: Path, mdir: Path) -> int:
    """Offline: install a zip from --export-pack. Every file must match PACK.json, and the committed pins when the
    model has one (a tampered or corrupted zip is refused before anything is copied)."""
    from . import downloader

    with tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(zpath) as z:
            for n in z.namelist():  # zip-slip guard
                if Path(n).is_absolute() or ".." in Path(n).parts:
                    sys.exit(f"unsafe path in pack: {n}")
            z.extractall(tmp)
        meta = json.loads((Path(tmp) / "PACK.json").read_text("utf-8"))
        pins = downloader.load_pins()
        for k, rec in meta["models"].items():
            got = downloader.hash_tree(Path(tmp) / "models" / k)
            if got != rec["files"]:
                sys.exit(f"SHA256 MISMATCH in pack for {k} - refusing.")
            pin = (pins["models"].get(k) or pins["argos"].get(k) or {}).get("files")
            if pin:
                downloader.check_pinned(pin, got, k)
        mdir.mkdir(parents=True, exist_ok=True)
        man = _manifest(mdir)
        for k, rec in meta["models"].items():
            dest = model_dir(mdir, k)
            if dest.exists():
                shutil.rmtree(dest)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(Path(tmp) / "models" / k, dest)
            man[k] = rec
        (mdir / "manifest.json").write_text(json.dumps(man, indent=1), "utf-8")
    print(f"pack {meta['lang']} imported ({len(meta['models'])} models)")
    return 0
