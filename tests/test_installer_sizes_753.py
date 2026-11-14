"""#753: the setup wizard's download sizes come from one named table in installer/tinglongwu.iss.

Inno Pascal cannot run on Linux (the compiled installer runs only in windows-build), so this file reads the table
(the MB constants and PackMB's case) and renders the language page labels and the progress text the way the
wizard's PackNote / SizeText / DownloadMB do. That pins the numbers to packs.py and install.ps1, and shows
the zh + en text is no longer "about 0 MB"."""
from __future__ import annotations

import re
from pathlib import Path

import importlib.util

from tinglongwu_setup import packs

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("build_installer", ROOT / "installer" / "build_installer.py")
_bi = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_bi)
# the source-layout wizard (no CoreLayout define): the core wizard (#771) has no language page or size table
ISS = _bi.preprocess((ROOT / "installer" / "tinglongwu.iss").read_text("utf-8-sig"), set())
INSTALL_PS1 = (ROOT / "install.ps1").read_text("utf-8-sig")


def consts() -> dict[str, int]:
    block = ISS.split("[Code]", 1)[1].split("\nvar", 1)[0]
    return {k: int(v) for k, v in re.findall(r"^\s*(\w+) = (\d+);", block, re.M)}


def _expr(e: str, c: dict[str, int]) -> int:
    return sum(c[t.strip()] if not t.strip().isdigit() else int(t) for t in e.split("+"))


def pack_mb(pack: int) -> int:
    """PackMB(Pack) of the .iss: its `case` arms over the PACK_* constants."""
    c = consts()
    body = ISS.split("function PackMB", 1)[1].split("end;\nend;", 1)[0]
    for labels, expr in re.findall(r"^\s*([\w, ]+): Result := ([\w +]+);", body, re.M):
        if pack in [c[x.strip()] for x in labels.split(",")]:
            return _expr(expr, c)
    return int(re.search(r"else Result := (\d+);", body).group(1))


def size_text(mb: int) -> str:
    tenths = (mb + 50) // 100  # SizeText: (MB + 50) div 100
    return f"about {tenths // 10}.{tenths % 10} GB"


def pack_note(pack: int) -> str:
    return ", " + size_text(pack_mb(pack)) if pack_mb(pack) > 0 else ""


def download_mb(gpu: bool, ticked: set[str]) -> int:
    c = consts()
    mb = (c["CORE_MODELS_GPU_MB"] if gpu else c["CORE_MODELS_CPU_MB"]) + c["PY_PACKAGES_MB"]
    return mb + (c["PIPER_RU_MB"] if "ru" in ticked else 0)


def labels() -> list[str]:
    """The language page options as the wizard shows them (PackNote evaluated)."""
    c = consts()
    out = []
    for head, pack, tail in re.findall(r"LangPage\.Add\('([^']*)'(?: \+ PackNote\((\w+)\) \+ '([^']*)')?\);", ISS):
        out.append(head + (pack_note(c[pack]) + tail if pack else ""))
    return out


def progress_text(gpu: bool, ticked: set[str]) -> str:
    m = re.search(r"ProgressPage\.Description := (.+);\n", ISS.split("procedure RunInstall", 1)[1])
    size = size_text(download_mb(gpu, ticked))
    return "".join(size if p.strip() == "Size" else p.strip()[1:-1] for p in m.group(1).split(" + "))


def test_zh_en_progress_text_shows_the_real_total_not_0_mb():
    text = progress_text(gpu=False, ticked={"zh", "en"})
    assert "0 MB" not in text and "about 0.0 GB" not in text
    assert "downloads about 5.0 GB" in text and "約下載 about 5.0 GB" in text
    assert "downloads about 6.0 GB" in progress_text(gpu=True, ticked={"zh", "en"})


def test_ru_adds_only_its_piper_voice_hymt2_is_in_the_core_already():
    assert download_mb(False, {"zh", "en", "ja"}) == download_mb(False, {"zh", "en"})
    assert download_mb(False, {"zh", "en", "ru"}) - download_mb(False, {"zh", "en"}) == packs.SIZE_MB[packs.PIPER_RU_DEFAULT]


def test_table_matches_packs_py_and_install_ps1():
    c = consts()
    assert c["HYMT2_MB"] == packs.SIZE_MB["hymt2-1.8b"]
    assert c["PIPER_RU_MB"] == packs.SIZE_MB[packs.PIPER_RU_DEFAULT]
    for i, code in enumerate(("zh", "en", "ja", "ko", "ru")):
        assert c["PACK_" + code.upper()] == i and pack_mb(i) == packs.pack_size_mb(code), code
    # install.ps1: "$SizeGB = '~2 GB'" (CPU tier) / "'~3 GB'" (GPU tier) and "~3 GB of Python packages"
    assert "$SizeGB = '~2 GB'" in INSTALL_PS1 and c["CORE_MODELS_CPU_MB"] == 2000
    assert "$SizeGB = '~3 GB'" in INSTALL_PS1 and c["CORE_MODELS_GPU_MB"] == 3000
    assert "plus ~3 GB of Python packages" in INSTALL_PS1 and c["PY_PACKAGES_MB"] == 3000


def test_language_labels_come_from_the_table_no_size_literals():
    got = labels()
    assert got[:5] == ["中文 Chinese (zh)", "English (en)", "日本語 Japanese (ja, about 1.1 GB)",
                       "한국어 Korean (ko, about 1.1 GB)", "Русский Russian (ru, about 1.2 GB)"]
    wizard = ISS.split("procedure InitializeWizard", 1)[1].split("\nend;", 1)[0]
    assert not re.search(r"\d+(\.\d+)? ?(MB|GB)", wizard.split("ProgressPage :=", 1)[0])
    run = ISS.split("procedure RunInstall", 1)[1].split("\nend;", 1)[0]
    assert not re.search(r"\d+(\.\d+)? ?(MB|GB)", run)
