"""Language packs (#178): core + per-language packs, lazy prompt, offline export/import. No network: the downloader's
per-model getters are replaced by fakes that write a small file and record it in models/manifest.json."""
import json
import zipfile

import pytest

from tinglongwu_setup import cli, downloader, packs

JA_RU = ("opus/ja-", "opus/ru-", "-ja/", "-ru/", "hymt2-1.8b/", "piper-ru_")


@pytest.fixture
def fake_net(monkeypatch):
    got = []

    def fake(key, d):
        def run(man, mdir, *a):
            dest = mdir / d(*a)
            dest.mkdir(parents=True, exist_ok=True)
            (dest / "model.bin").write_bytes(key(*a).encode())
            man[key(*a)] = {"files": downloader.hash_tree(dest)}
            got.append(key(*a))
        return run

    monkeypatch.setattr(downloader, "get_pair", fake(lambda p: "opus-" + p, lambda p: f"opus/{p}"))
    monkeypatch.setattr(downloader, "get_hymt", fake(lambda: "hymt2-1.8b", lambda: "hymt2-1.8b"))
    monkeypatch.setattr(downloader, "get_piper_ru",
                        fake(lambda v: downloader.piper_ru_dir(v), lambda v: downloader.piper_ru_dir(v)))
    monkeypatch.setattr(downloader, "load_pins", lambda: {"models": {}, "argos": {}})
    return got


def listing(mdir):
    return sorted(p.relative_to(mdir).as_posix() + ("/" if p.is_dir() else "") for p in mdir.rglob("*"))


def test_zh_en_install_has_no_ja_or_ru_files(tmp_path, fake_net):
    assert cli.main(["--packs", "zh,en", "--models-dir", str(tmp_path)]) == 0
    files = listing(tmp_path)
    assert "opus/en-zh/model.bin" in files and "opus/zh-en/model.bin" in files
    assert [f for f in files if any(m in f for m in JA_RU)] == []
    assert sorted(fake_net) == ["opus-en-zh", "opus-zh-en"]  # zh and en share the pairs: fetched once


def test_list_shows_installed_and_missing(tmp_path, fake_net, capsys):
    cli.main(["--packs", "zh,en", "--models-dir", str(tmp_path)])
    capsys.readouterr()
    assert cli.main(["--list", "--models-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out.splitlines()
    assert "zh  installed" in out and "en  installed" in out
    assert any(line.startswith("ja  missing (~1200 MB)") for line in out)


def test_selecting_ja_prompts_then_downloads(tmp_path, fake_net, monkeypatch, capsys):
    cli.main(["--packs", "zh,en", "--models-dir", str(tmp_path)])
    asked = []
    monkeypatch.setattr("builtins.input", lambda q: asked.append(q) or "y")
    assert cli.main(["--pack-prompt", "ja", "--models-dir", str(tmp_path)]) == 0
    assert asked == ["pack ja (~1200 MB) missing — download now? [y/N] "]
    capsys.readouterr()
    cli.main(["--list", "--models-dir", str(tmp_path)])
    assert "ja  installed" in capsys.readouterr().out.splitlines()
    asked.clear()
    assert cli.main(["--pack-prompt", "ja", "--models-dir", str(tmp_path)]) == 0 and asked == []  # no re-ask


def test_prompt_default_is_no(tmp_path, fake_net, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda q: "")
    assert cli.main(["--pack-prompt", "ru", "--models-dir", str(tmp_path)]) == 1
    assert fake_net == [] and listing(tmp_path) == []


def test_prompt_follows_ui_language(tmp_path, monkeypatch):
    monkeypatch.setenv("TLWU_UI_LANG", "zh-TW")
    asked = []
    assert packs.prompt("ru", tmp_path, ask=lambda q: asked.append(q) or "n") == 1
    assert asked == ["語言包 ru（約 260 MB）尚未安裝 — 現在下載嗎？[y/N] "]


def test_unknown_pack_is_refused(tmp_path):
    with pytest.raises(SystemExit):
        cli.main(["--pack-prompt", "xx", "--models-dir", str(tmp_path)])


def test_export_then_import_on_another_machine(tmp_path, fake_net, capsys):
    a, b, z = tmp_path / "a", tmp_path / "b", tmp_path / "ja.zip"
    cli.main(["--packs", "ja", "--models-dir", str(a)])
    assert cli.main(["--export-pack", "ja", str(z), "--models-dir", str(a)]) == 0
    fake_net.clear()
    assert cli.main(["--import-pack", str(z), "--models-dir", str(b)]) == 0
    assert fake_net == []  # nothing downloaded
    assert (b / "hymt2-1.8b" / "model.bin").read_bytes() == b"hymt2-1.8b"
    capsys.readouterr()
    cli.main(["--list", "--models-dir", str(b)])
    assert "ja  installed" in capsys.readouterr().out.splitlines()
    assert downloader.main(["--verify", "--models-dir", str(b)]) == 0


def test_import_refuses_tampered_zip(tmp_path, fake_net):
    a, z, bad = tmp_path / "a", tmp_path / "ja.zip", tmp_path / "bad.zip"
    cli.main(["--packs", "ja", "--models-dir", str(a)])
    cli.main(["--export-pack", "ja", str(z), "--models-dir", str(a)])
    with zipfile.ZipFile(z) as src, zipfile.ZipFile(bad, "w") as dst:
        for n in src.namelist():
            dst.writestr(n, b"evil" if n == "models/hymt2-1.8b/model.bin" else src.read(n))
    with pytest.raises(SystemExit):
        cli.main(["--import-pack", str(bad), "--models-dir", str(tmp_path / "b")])
    assert not (tmp_path / "b" / "hymt2-1.8b").exists()


def test_import_checks_committed_pins(tmp_path, fake_net, monkeypatch):
    a, z = tmp_path / "a", tmp_path / "ja.zip"
    cli.main(["--packs", "ja", "--models-dir", str(a)])
    cli.main(["--export-pack", "ja", str(z), "--models-dir", str(a)])
    monkeypatch.setattr(downloader, "load_pins", lambda: {
        "models": {"hymt2-1.8b": {"files": {"model.bin": "0" * 64}}}, "argos": {}})
    with pytest.raises(SystemExit):  # consistent with its own PACK.json, but not the real pinned model
        cli.main(["--import-pack", str(z), "--models-dir", str(tmp_path / "b")])


def test_import_rejects_zip_slip(tmp_path):
    z = tmp_path / "p.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("PACK.json", json.dumps({"lang": "ja", "models": {}}))
        zf.writestr("../evil.txt", "x")
    with pytest.raises(SystemExit):
        cli.main(["--import-pack", str(z), "--models-dir", str(tmp_path / "m")])
    assert not (tmp_path / "evil.txt").exists()


def test_tier_downloads_only_default_packs():
    from tlwu_app import tiers

    assert "--packs" in tiers.T_CPU.downloader_args() and "zh,en" in tiers.T_CPU.downloader_args()
    assert not any(x in " ".join(tiers.T_GPU.downloader_args()) for x in ("ja", "ru"))


@pytest.mark.parametrize("models", [
    {"../../victim": {"files": {}}},                      # path escape with an empty file list (empty hash tree)
    {"opus-ja-en": {"files": {}}, "hymt2-1.8b": {"files": {}}},  # right keys, no files
    {"opus-ja-en": {"files": {"a": "0" * 64}}},           # key set != PACKS["ja"]
])
def test_import_refuses_pack_json_not_matching_the_pack(tmp_path, models):
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "keep.txt").write_text("x")
    z, mdir = tmp_path / "evil.zip", tmp_path / "m" / "models"
    with zipfile.ZipFile(z, "w") as zz:
        zz.writestr("PACK.json", json.dumps({"lang": "ja", "models": models}))
    with pytest.raises(SystemExit):
        cli.main(["--import-pack", str(z), "--models-dir", str(mdir)])
    assert (victim / "keep.txt").exists()
    assert not mdir.exists()
