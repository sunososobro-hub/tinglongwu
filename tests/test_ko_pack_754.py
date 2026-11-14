"""#754: Korean is a desktop language pack like ja - setup wizard option, OS-locale pre-check, ko->zh eval slice."""
from __future__ import annotations

import io
import json
import re
import tarfile
from pathlib import Path

from tinglongwu_setup import packs
from tlwu_eval.runner import load_manifest

from test_make_ci_smoke import REV, FakeHub, _tsv, _wav, mcs  # shared fake FLEURS hub

ROOT = Path(__file__).resolve().parents[1]
ISS = (ROOT / "installer" / "tinglongwu.iss").read_text("utf-8-sig")


def _wizard() -> tuple[list[str], list[str], dict[int, int]]:
    """The Windows wizard's language page as data: option labels, the --packs code of each index, and the
    OS primary-language-id -> pre-checked index table. Inno Pascal cannot run on Linux, so the compiled installer
    (windows-build) is the only place it executes; this pins the wizard to packs.py so the two cannot drift."""
    labels = re.findall(r"LangPage\.Add\('([^']*)'\)", ISS)
    codes = dict((int(i), c) for i, c in re.findall(r"Codes\[(\d+)\] := '([a-z]+)'", ISS))
    body = ISS.split("function LocaleLangIndex", 1)[1].split("end;", 1)[0]
    locale = {int(k, 16): int(v) for k, v in re.findall(r"\$([0-9A-Fa-f]+): Result := (\d+);", body)}
    return labels, [codes[i] for i in sorted(codes)], locale


def test_wizard_offers_every_tier1_pack_with_its_size():
    labels, codes, _ = _wizard()
    assert sorted(codes) == sorted(packs.TIER1)
    for i, code in enumerate(codes):
        assert f"({code}" in labels[i], (i, code, labels[i])
        mb = packs.pack_size_mb(code)
        if mb:
            assert f"about {mb / 1000:.1f} GB" in labels[i], labels[i]


def test_wizard_has_korean_option():
    labels, codes, _ = _wizard()
    assert labels[codes.index("ko")] == "한국어 Korean (ko, about 1.1 GB)"


def test_korean_os_prechecks_ko():
    _, codes, locale = _wizard()
    # Windows primary language ids: 0x04 Chinese, 0x11 Japanese, 0x12 Korean, 0x19 Russian
    assert {k: codes[v] for k, v in locale.items()} == {0x04: "zh", 0x11: "ja", 0x12: "ko", 0x19: "ru"}


def test_gpu_option_follows_the_language_options():
    labels, codes, _ = _wizard()
    gpu = len(codes)
    assert "GPU" in labels[gpu]
    assert f"LangPage.Values[{gpu}] := HasNvidiaDriver" in ISS and f"(not LangPage.Values[{gpu}])" in ISS


def test_cli_installers_pick_ko_on_korean_locale():
    env = {"LANG": "ko_KR.UTF-8", "TLWU_YES": "1"}
    assert packs.locale_default(env) == "ko"
    assert packs.choose_langs(env) == "ko"
    assert packs.total_size_mb("ja,ko") == 1100  # Hy-MT2 shared, counted once
    assert packs.keys_for("ko") == ["hymt2-1.8b"]


class KoHub(FakeHub):
    def __init__(self):
        super().__init__()
        self.files[mcs.url_for(REV, "ko_kr", "test.tsv")] = _tsv("ko")
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as t:
            for i in range(5):
                data = _wav(i + 70)
                info = tarfile.TarInfo(f"test/ko{i}.wav")
                info.size = len(data)
                t.addfile(info, io.BytesIO(data))
        self.files[mcs.url_for(REV, "ko_kr", "audio/test.tar.gz")] = buf.getvalue()


def test_ko_slice_pin_then_build_gives_zh_tw_refs(tmp_path):
    pins = tmp_path / "ko_pins.json"
    hub = KoHub()
    assert mcs.main(["--set", "ko", "--pin", "5", "--revision", REV, "--pins", str(pins)], fetch=hub) == 0
    pin = json.loads(pins.read_text("utf-8"))
    assert pin["configs"] == {"ko": "ko_kr"} and [c["lang"] for c in pin["clips"]] == ["ko"] * 5
    out = tmp_path / "ko"
    assert mcs.main(["--out", str(out), "--pins", str(pins)], fetch=hub) == 0
    rows = load_manifest(out)
    assert len(rows) == 5 and {r["lang"] for r in rows} == {"ko"}
    for r in rows:
        assert r["ref_zh"] == f"這個國家的軟體發展很快{r['flores_id'] - 100}。"
        assert r["source"].startswith("fleurs/ko_kr/")
