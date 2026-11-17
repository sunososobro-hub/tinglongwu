"""#558: optional 對話翻譯 packs on mobile-latest are rebuilt by CI only when their
voice_packs.json entry changes (tool/make_packs.py stale / mark), and the TTS latency helper."""
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import zipfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(ROOT, "mobile", "tool")


def _load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(TOOL, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mp = _load("make_packs")
lat = _load("tts_latency")
PACKS = mp.load_packs()
ALL_ZIPS = {p["name"] for p in PACKS}


def test_manifest_has_the_three_conversation_packs():
    assert {p["id"] for p in PACKS} == {"voice-en", "voice-ru", "lang-ru"}


def test_no_markers_means_every_pack_is_rebuilt():
    assert mp.stale(PACKS, "release notes without markers", ALL_ZIPS) == [p["id"] for p in PACKS]


def test_marked_body_is_up_to_date_and_mark_is_idempotent():
    body = mp.mark(PACKS, "notes\n", [p["id"] for p in PACKS])
    assert mp.stale(PACKS, body, ALL_ZIPS) == []
    assert mp.mark(PACKS, body, ["voice-ru"]) == body
    assert body.count("<!-- tlwu-pack:") == 3


def test_missing_zip_is_rebuilt_even_when_marked():
    body = mp.mark(PACKS, "", [p["id"] for p in PACKS])
    assert mp.stale(PACKS, body, ALL_ZIPS - {"tlwu-voice-ru.zip"}) == ["voice-ru"]


def test_changed_entry_rebuilds_only_that_pack():
    body = mp.mark(PACKS, "", [p["id"] for p in PACKS])
    changed = json.loads(json.dumps(PACKS))
    changed[0]["files"][0]["sha256"] = "0" * 64
    assert mp.stale(changed, body, ALL_ZIPS) == [changed[0]["id"]]
    # re-marking it replaces the old marker instead of adding a second one
    body2 = mp.mark(changed, body, [changed[0]["id"]])
    assert mp.stale(changed, body2, ALL_ZIPS) == []
    assert body2.count(f"tlwu-pack:{changed[0]['id']}:") == 1


def test_digest_ignores_key_order():
    p = PACKS[0]
    assert mp.pack_digest(dict(reversed(list(p.items())))) == mp.pack_digest(p)


def test_mark_unknown_pack_fails():
    with pytest.raises(KeyError):
        mp.mark(PACKS, "", ["voice-xx"])


def test_cli_stale_then_mark(tmp_path):
    body = tmp_path / "body.md"
    body.write_text("**1. Install the app**\n<!-- tlwu-run:9 -->\n", encoding="utf-8")
    cli = [sys.executable, "-I", os.path.join(TOOL, "make_packs.py")]
    out = subprocess.run(cli + ["stale", str(body), *sorted(ALL_ZIPS)], capture_output=True, text=True, check=True)
    ids = out.stdout.split()
    assert ids == [p["id"] for p in PACKS]
    subprocess.run(cli + ["mark", str(body), *ids], check=True)
    out = subprocess.run(cli + ["stale", str(body), *sorted(ALL_ZIPS)], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == ""
    assert "<!-- tlwu-run:9 -->" in body.read_text(encoding="utf-8")


def test_build_checks_files_and_renames_alias(tmp_path):
    # a fake upstream folder whose files match a small manifest entry (no download)
    src = tmp_path / "src" / "fake-voice"
    src.mkdir(parents=True)
    (src / "upstream.onnx").write_bytes(b"weights")
    (src / "tokens.txt").write_bytes(b"a 1\n")
    (src / "LICENSE").write_text("Apache-2.0")

    def entry(path, data, alias=None):
        e = {"path": path, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        if alias:
            e["alias"] = alias
        return e

    pack = {
        "id": "voice-en",
        "name": "tlwu-voice-xx.zip",
        "source": "https://example.invalid/fake-voice.tar.bz2",
        "files": [entry("tts-xx/model.onnx", b"weights", "upstream.onnx"), entry("tts-xx/tokens.txt", b"a 1\n")],
    }
    out = mp.build(pack, str(tmp_path / "src"), str(tmp_path / "out"))
    with zipfile.ZipFile(out) as z:
        names = set(z.namelist())
        assert {i.compress_type for i in z.infolist()} == {zipfile.ZIP_STORED}
    assert {"tlwu-voice-xx/model.onnx", "tlwu-voice-xx/tokens.txt",
            "tlwu-voice-xx/NOTICE.txt", "tlwu-voice-xx/UPSTREAM-LICENSE"} <= names

    pack["files"][1]["sha256"] = "0" * 64
    with pytest.raises(SystemExit):
        mp.build(pack, str(tmp_path / "src"), str(tmp_path / "out"))


def test_latency_summary():
    s = lat.summarize([(400, 1400), (900, 3000), (1600, 5200)])
    assert s == {"n": 3, "gen_median_ms": 900, "gen_max_ms": 1600, "rtf": round(2900 / 9600, 3)}
    with pytest.raises(ValueError):
        lat.summarize([])


def test_latency_pack_files_resolve_alias(tmp_path):
    (tmp_path / "vits-ljs.onnx").write_bytes(b"x")
    en = next(p for p in PACKS if p["id"] == "voice-en")
    files = lat.pack_files(en, str(tmp_path))
    assert files["model.onnx"] == str(tmp_path / "vits-ljs.onnx")
    assert files["tokens.txt"] == str(tmp_path / "tokens.txt")
