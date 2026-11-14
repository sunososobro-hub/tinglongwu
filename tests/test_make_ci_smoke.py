"""#569: scripts/bench/make_ci_smoke.py rebuilds the smoke set from pinned FLEURS clips (offline, fake downloader)."""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import struct
import tarfile
from pathlib import Path

import pytest

from tlwu_eval.runner import load_manifest

ROOT = Path(__file__).resolve().parents[1]
REV = "0" * 40
LANGS = ("en", "ja", "ru", "zh")
# Simplified cmn transcripts (FLoRes id -> text); ref_zh must come out in Taiwan Traditional.
CMN = {100 + i: f"这个国家的软件发展很快{i}。" for i in range(5)}


def _load():
    spec = importlib.util.spec_from_file_location("make_ci_smoke_569", ROOT / "scripts" / "bench" / "make_ci_smoke.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mcs = _load()


def _wav(seed: int, seconds: float = 1.0) -> bytes:
    """32-bit float 16 kHz mono WAV, like FLEURS ships."""
    n = int(16000 * seconds)
    data = struct.pack(f"<{n}f", *([seed / 100.0] * n))
    fmt = struct.pack("<HHIIHH", 3, 1, 16000, 64000, 4, 32)
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(data)) + data
    return b"RIFF" + struct.pack("<I", len(body)) + body


def _tsv(lang: str) -> bytes:
    lines = []
    for i in range(5):
        fid = 100 + i
        text = CMN[fid] if lang == "zh" else f"{lang} sentence {i}."
        lines.append(f"{fid}\t{lang}{i}.wav\t{text}\t{text.lower()}\tx\t16000\tMALE")
    return "\n".join(lines).encode("utf-8")


def _tar(lang: str) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for i in range(5):
            data = _wav(i + 10 * LANGS.index(lang))
            info = tarfile.TarInfo(f"test/{lang}{i}.wav")
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class FakeHub:
    def __init__(self):
        self.files = {}
        for lang, cfg in mcs.CONFIGS.items():
            self.files[mcs.url_for(REV, cfg, "test.tsv")] = _tsv(lang)
            self.files[mcs.url_for(REV, cfg, "audio/test.tar.gz")] = _tar(lang)
        self.urls = []

    def __call__(self, url):
        self.urls.append(url)
        return io.BytesIO(self.files[url])


def _pin() -> dict:
    clips = []
    for k, lang in enumerate(LANGS):
        for i in range(5):
            clips.append({"id": f"{lang}_{i + 1:03d}", "lang": lang, "file": f"{lang}{i}.wav", "flores_id": 100 + i,
                          "sha256": hashlib.sha256(_wav(i + 10 * k)).hexdigest()})
    return {"dataset": mcs.DATASET, "revision": REV, "clips": clips}


def _write_pin(tmp_path, pin) -> str:
    p = tmp_path / "ci_smoke.json"
    p.write_text(json.dumps(pin), encoding="utf-8")
    return str(p)


def test_builds_20_rows_load_manifest_accepts(tmp_path):
    out = tmp_path / "smoke"
    hub = FakeHub()
    assert mcs.main(["--out", str(out), "--pins", _write_pin(tmp_path, _pin())], fetch=hub) == 0
    rows = load_manifest(out)
    assert len(rows) == 20
    assert {lang: sum(r["lang"] == lang for r in rows) for lang in LANGS} == {lang: 5 for lang in LANGS}
    for r in rows:
        assert Path(r["wav"]).is_file()
        assert r["ref_text"]
        if r["lang"] == "zh":
            assert r["ref_en"] == f"en sentence {r['flores_id'] - 100}." and "ref_zh" not in r
        else:
            # cmn transcript of the same FLoRes id, converted to Taiwan Traditional
            assert r["ref_zh"] == f"這個國家的軟體發展很快{r['flores_id'] - 100}。"
    assert (out / "LICENSE.txt").read_text("utf-8").count(REV) == 1
    assert all(REV in u for u in hub.urls)


def test_wavs_match_pinned_sha256(tmp_path):
    out = tmp_path / "smoke"
    pin = _pin()
    mcs.build(out, pin, fetch=FakeHub())
    for c in pin["clips"]:
        assert hashlib.sha256((out / f"{c['id']}.wav").read_bytes()).hexdigest() == c["sha256"]


def test_sha256_mismatch_exits_nonzero_without_manifest(tmp_path, capsys):
    pin = _pin()
    pin["clips"][7]["sha256"] = "f" * 64
    out = tmp_path / "smoke"
    assert mcs.main(["--out", str(out), "--pins", _write_pin(tmp_path, pin)], fetch=FakeHub()) == 1
    assert not (out / "manifest.jsonl").exists()
    assert "ja_003: sha256" in capsys.readouterr().err


def test_missing_clip_exits_nonzero(tmp_path):
    pin = _pin()
    pin["clips"][0]["file"] = "gone.wav"
    assert mcs.main(["--out", str(tmp_path / "s"), "--pins", _write_pin(tmp_path, pin)], fetch=FakeHub()) == 1


def test_download_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(mcs, "MAX_DOWNLOAD", 1000)
    assert mcs.main(["--out", str(tmp_path / "s"), "--pins", _write_pin(tmp_path, _pin())], fetch=FakeHub()) == 1


def test_pin_reproduces_and_skips_long_clips(tmp_path, monkeypatch):
    monkeypatch.setattr(mcs, "MAX_CLIP_S", 0.5)  # every fake clip is 1 s long
    with pytest.raises(mcs.DownloadError):
        mcs.pin_clips(REV, 5, fetch=FakeHub())
    monkeypatch.setattr(mcs, "MAX_CLIP_S", 15.0)
    pins = tmp_path / "pins.json"
    assert mcs.main(["--pin", "5", "--revision", REV, "--pins", str(pins)], fetch=FakeHub()) == 0
    pin = json.loads(pins.read_text("utf-8"))
    assert [(c["id"], c["sha256"]) for c in pin["clips"]] == [(c["id"], c["sha256"]) for c in _pin()["clips"]]
    assert mcs.main(["--out", str(tmp_path / "s"), "--pins", str(pins)], fetch=FakeHub()) == 0


def test_check_wav_rejects_wrong_rate():
    bad = bytearray(_wav(1))
    bad[24:28] = struct.pack("<I", 44100)
    with pytest.raises(ValueError):
        mcs.check_wav(bytes(bad))
    assert mcs.check_wav(_wav(1, 2.0)) == pytest.approx(2.0)


# #602: the tier-2 set (vi/th/id/es/de/fr -> zh) uses the same tool; the pin file carries its own configs map.
T2 = tuple(mcs.TIER2_CONFIGS)


class Tier2Hub(FakeHub):
    def __init__(self):
        super().__init__()
        for k, (lang, cfg) in enumerate(mcs.TIER2_CONFIGS.items()):
            self.files[mcs.url_for(REV, cfg, "test.tsv")] = _tsv(lang)
            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w:gz") as t:
                for i in range(5):
                    data = _wav(i + 10 * (k + 4))
                    info = tarfile.TarInfo(f"test/{lang}{i}.wav")
                    info.size = len(data)
                    t.addfile(info, io.BytesIO(data))
            self.files[mcs.url_for(REV, cfg, "audio/test.tar.gz")] = buf.getvalue()


def test_tier2_pin_then_build_gives_zh_refs_per_language(tmp_path):
    pins = tmp_path / "tier2_pins.json"
    hub = Tier2Hub()
    assert mcs.main(["--set", "tier2", "--pin", "5", "--revision", REV, "--pins", str(pins)], fetch=hub) == 0
    pin = json.loads(pins.read_text("utf-8"))
    assert pin["configs"] == mcs.TIER2_CONFIGS
    assert {lang: sum(c["lang"] == lang for c in pin["clips"]) for lang in T2} == {lang: 5 for lang in T2}
    # only tier-2 archives were streamed (plus the zh tsv for references), never the smoke languages' audio
    assert not any("audio" in u and any(f"/{c}/" in u for c in mcs.CONFIGS.values()) for u in hub.urls)
    out = tmp_path / "t2"
    assert mcs.main(["--out", str(out), "--pins", str(pins)], fetch=hub) == 0
    rows = load_manifest(out)
    assert sorted({r["lang"] for r in rows}) == sorted(T2) and len(rows) == 30
    for r in rows:
        assert r["ref_zh"] == f"這個國家的軟體發展很快{r['flores_id'] - 100}。"
        assert r["source"].startswith(f"fleurs/{mcs.TIER2_CONFIGS[r['lang']]}/")
    assert "tier2_pins.json" in (out / "LICENSE.txt").read_text("utf-8")


def test_tier2_pin_with_unknown_language_fails(tmp_path):
    pin = {"revision": REV, "configs": mcs.TIER2_CONFIGS, "clips": [{"id": "ko_001", "lang": "ko", "file": "x.wav",
                                                                    "sha256": "0" * 64}]}
    assert mcs.main(["--out", str(tmp_path / "s"), "--pins", _write_pin(tmp_path, pin)], fetch=Tier2Hub()) == 1


def test_repo_tier2_pins_have_20_clips_per_language():
    pin = json.loads((ROOT / "eval" / "tier2_pins.json").read_text("utf-8"))
    assert pin["configs"] == mcs.TIER2_CONFIGS
    assert {lang: sum(c["lang"] == lang for c in pin["clips"]) for lang in T2} == {lang: 20 for lang in T2}
    assert all(len(c["sha256"]) == 64 for c in pin["clips"])


def test_missing_pin_file_exits_with_a_clear_message(tmp_path, capsys):
    # #789: a missing --pins file is a clear error, not an uncaught FileNotFoundError
    missing = tmp_path / "nope.json"
    assert mcs.main(["--set", "ko", "--pins", str(missing), "--out", str(tmp_path / "o")]) == 1
    err = capsys.readouterr().err
    assert f"pin file not found: {missing}" in err and "--set ko --pin N" in err
