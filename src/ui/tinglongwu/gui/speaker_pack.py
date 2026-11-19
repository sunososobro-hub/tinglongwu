"""0.3.8: the optional speaker-ID pack (sherpa-onnx + speaker model), offered when the 「標示說話者」 toggle is turned on.
No tkinter here (the GUI passes `ask` / `start`). Nothing of this is in the default install; with the toggle off
neither sherpa-onnx nor the model is loaded, even when both are installed."""
from __future__ import annotations

import sys
from pathlib import Path

# Measured (PyPI JSON / release asset, 2026-11): sherpa-onnx 2.0.1 win_amd64 wheel per CPython tag + sherpa-onnx-core
# win_amd64 wheel + the CAM++ model (tinglongwu_setup.downloader.SPEAKER_FILES, 28,281,164 bytes).
WHEEL_BYTES = {"cp310": 2174131, "cp311": 2173799, "cp312": 2178304, "cp313": 2179778}
WHEEL_DEFAULT = 2200000
CORE_BYTES = 16663244
MODEL_BYTES = 28281164
MODEL_NAME = "3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx"  # keep in sync with speaker_embed
SPEAKER_DIR = "speaker"

TEXT = {
    "en": "Identify speakers needs a small add-on (about {mb} MB). Download it now? Needs internet once; works "
          "offline afterwards.",
    "ru": "Для определения говорящих нужен небольшой пакет (около {mb} МБ). Скачать сейчас? Интернет нужен один раз.",
    "zh-TW": "要下載多人分辨（約 {mb} MB）嗎？只需連網下載一次，之後可離線使用。",
    "ja": "話者の識別には追加パック（約 {mb} MB）が必要です。今ダウンロードしますか？ダウンロードは一度だけです。",
}
BUTTONS = {"en": ("Download", "Not now"), "ru": ("Скачать", "Не сейчас"), "zh-TW": ("下載", "稍後"),
           "ja": ("ダウンロード", "あとで")}


def pkg_installed(mdir=None, bundled: bool | None = None) -> bool:
    """Package present. Packaged build: models/speaker/pylib must carry a valid .pins marker (a half-extracted
    folder does not count) before it is put on sys.path."""
    from ..speaker_pylib import module_present

    if mdir is not None:
        from ..speaker_pylib import activate, pylib_valid  # only reached with the toggle on

        if (is_frozen() if bundled is None else bundled) and not pylib_valid(mdir):
            return False
        if pylib_valid(mdir) or not (Path(mdir) / "speaker" / "pylib" / ".pins").exists():
            activate(mdir)

    return module_present("sherpa_onnx")


def model_installed(mdir) -> bool:
    return (Path(mdir) / SPEAKER_DIR / MODEL_NAME).is_file()


def import_ok(mdir) -> bool:
    """The package really imports (after the pylib path is set); a DLL problem shows up here, not later."""
    try:
        from ..speaker_pylib import activate

        activate(mdir)
        import sherpa_onnx  # noqa: F401 - optional speaker pack; a missing or broken one counts as not installed

        return True
    except Exception:  # noqa: BLE001
        return False


def missing(mdir, pkg_ok=None, model_ok=model_installed) -> tuple[bool, bool]:
    """(package missing, model missing)."""
    if pkg_ok is None:
        def pkg_ok():  # installed AND really imports (a broken DLL set counts as missing -> repair download)
            return pkg_installed(mdir) and import_ok(mdir)
    return (not pkg_ok(), not model_ok(mdir))


def is_frozen() -> bool:
    """Packaged build? The shipped build is a C launcher + embedded Python (sys.frozen is never set): use the
    same detection as the updater (TLWU_BUNDLED / sys.executable is tlwu.exe)."""
    from .autoupdate import is_bundled

    return is_bundled()


def size_mb(mdir, frozen: bool | None = None, pytag: str | None = None, **kw) -> int:
    """MB still to download: wheel + core + model (same for source and packaged builds; `frozen` is ignored)."""
    no_pkg, no_model = missing(mdir, **kw)
    total = 0
    if no_pkg:
        tag = pytag or f"cp{sys.version_info.major}{sys.version_info.minor}"
        total += WHEEL_BYTES.get(tag, WHEEL_DEFAULT) + CORE_BYTES
    if no_model:
        total += MODEL_BYTES
    return max(1, round(total / 1_000_000))


def dialog_text(ui_lang: str, mb: int) -> str:
    return TEXT.get(ui_lang, TEXT["en"]).format(mb=mb)


def on_toggle(want: bool, mdir, ask, start, ui_lang: str = "en", **kw) -> str:
    """The toggle was clicked. Returns "off" (nothing to do / declined), "on" (everything present) or "downloading"
    (`start()` was called; the GUI finishes in pack_done). `ask(text, ok_label, no_label) -> bool`."""
    if not want:
        return "off"
    no_pkg, no_model = missing(mdir, **{k: v for k, v in kw.items() if k in ("pkg_ok", "model_ok")})
    if not (no_pkg or no_model):
        return "on"
    yes, no = BUTTONS.get(ui_lang, BUTTONS["en"])
    if not ask(dialog_text(ui_lang, size_mb(mdir, **kw)), yes, no):
        return "off"
    start()
    return "downloading"


def make_labeler(settings: dict, mdir, on_ready=None, factory=None, clusterer=None):
    """The listen-leg labeler, or None. Toggle off -> None WITHOUT importing or loading sherpa / the model."""
    if not settings.get("speaker_id"):
        return None
    if factory is None:
        from ..speaker_embed import make_async_labeler as factory
    if clusterer is not None:  # one clusterer per session: numbering / names / colors survive off -> on
        return factory(mdir, on_ready=on_ready, clusterer=clusterer)
    return factory(mdir, on_ready=on_ready)


def requirements_file(tmpdir=None) -> Path:
    """The pinned requirement list, written from the copy embedded in speaker_reqs (requirements-speaker.txt is not
    part of a non-editable install)."""
    import tempfile

    from ..speaker_reqs import REQUIREMENTS

    f = Path(tempfile.mkdtemp(prefix="tlwu-spk-", dir=tmpdir)) / "requirements-speaker.txt"
    f.write_text(REQUIREMENTS, "utf-8")
    return f


def pip_cmd(req: str | Path) -> list[str]:
    return [sys.executable, "-m", "pip", "install", "--only-binary=:all:", "--require-hashes", "--force-reinstall",
            "-r", str(req)]


def model_cmd(mdir, bundled: bool | None = None, pylib: bool = False) -> list[str]:
    from .core import download_cmd

    cmd = download_cmd("small", mdir, bundled)
    i = cmd.index("--whisper")
    return [*cmd[:i], "--speaker-model", *(["--speaker-pylib"] if pylib else []), *cmd[i + 2:]]


def run_download(put, mdir, req, frozen: bool | None = None, runner=None, **kw) -> int:
    """Worker thread: pip install (source run only) then the model child; `put` gets ("pack_line", text) lines and one
    final ("pack_done", "speaker", rc). A packaged build cannot pip-install: its child unzips the pinned wheels into
    models/speaker/pylib instead (`--speaker-pylib`)."""
    from .core import run_pack_download

    runner = runner or run_pack_download
    frozen = is_frozen() if frozen is None else frozen
    no_pkg, no_model = missing(mdir, **kw)
    rc = 0
    last = [""]  # the last child output line = the real reason when it fails
    if req is None:
        req = requirements_file()

    def inner(ev):
        if ev[0] != "pack_done":
            if ev[0] == "pack_line" and len(ev) > 1 and str(ev[1]).strip():
                last[0] = str(ev[1]).strip()[:120]
            put(ev)

    if no_pkg and frozen:  # absent / half-extracted / broken: start from a clean folder
        import shutil

        from ..speaker_pylib import pylib_dir

        shutil.rmtree(pylib_dir(mdir), ignore_errors=True)

    try:
        if no_pkg and not frozen:
            rc = runner("speaker", inner, cmd=pip_cmd(req))
        if rc == 0 and (no_model or (no_pkg and frozen)):
            rc = runner("speaker", inner, mdir=mdir, cmd=model_cmd(mdir, pylib=no_pkg and frozen))
    except Exception as e:  # noqa: BLE001
        put(("pack_line", f"{type(e).__name__}: {e}"[:90]))
        rc = -1
    if rc != 0:
        put(("pack_fail_reason", "speaker", last[0]))
    put(("pack_done", "speaker", rc))
    return rc
