import sys
import types

from tinglongwu_setup import downloader as dl

GGUF = "Hy-MT2-1.8B-Q4_K_M.gguf"


def _fake_hf(monkeypatch, calls, files):
    def snapshot_download(repo, revision=None, local_dir=None, allow_patterns=None):
        calls.append((repo, revision, list(allow_patterns)))
        from pathlib import Path
        d = Path(local_dir)
        d.mkdir(parents=True, exist_ok=True)
        for f in allow_patterns:
            if f in files:
                (d / f).write_text(files[f])

    monkeypatch.setitem(sys.modules, "huggingface_hub", types.SimpleNamespace(snapshot_download=snapshot_download))


def _pin(monkeypatch):
    monkeypatch.setattr(dl, "load_pins", lambda: {"models": {}, "argos": {}})
    monkeypatch.setattr(dl, "resolve_hf", lambda man, key, repo: (repo, "rev123", None))


def test_hymt_license_downloaded_with_gguf(tmp_path, monkeypatch):
    calls, man = [], {}
    _fake_hf(monkeypatch, calls, {GGUF: "gguf", "LICENSE.txt": "Apache"})
    _pin(monkeypatch)
    dl.get_hymt(man, tmp_path)
    assert calls == [("tencent/Hy-MT2-1.8B-GGUF", "rev123", [GGUF, "LICENSE.txt"])]
    assert (tmp_path / "hymt2-1.8b" / "LICENSE.txt").read_text() == "Apache"
    assert list(man["hymt2-1.8b"]["files"]) == [GGUF]  # license is not pinned


def test_hymt_existing_install_without_license_only_fetches_license(tmp_path, monkeypatch):
    calls, man = [], {}
    _fake_hf(monkeypatch, calls, {GGUF: "gguf", "LICENSE.txt": "Apache"})
    _pin(monkeypatch)
    dl.get_hymt(man, tmp_path)
    (tmp_path / "hymt2-1.8b" / "LICENSE.txt").unlink()
    calls.clear()
    dl.get_hymt(man, tmp_path)  # must not raise SHA mismatch, must not refetch the GGUF
    assert calls == [("tencent/Hy-MT2-1.8B-GGUF", "rev123", ["LICENSE.txt"])]
    assert (tmp_path / "hymt2-1.8b" / "LICENSE.txt").exists()
