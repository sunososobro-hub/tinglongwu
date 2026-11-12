"""#337: every model/voice tlwu-setup can download is listed in THIRD_PARTY_NOTICES.md; Apache text + links ship."""
import json
from pathlib import Path

from tinglongwu_setup import downloader as dl

ROOT = Path(__file__).resolve().parents[1]
NOTICES = (ROOT / "THIRD_PARTY_NOTICES.md").read_text("utf-8")


def _ids():
    man = json.loads((ROOT / "tinglongwu_setup" / "manifest.json").read_text("utf-8"))
    ids = {m["repo"] for m in man["models"].values()}
    ids |= {m["url"] for m in man["argos"].values()}
    ids |= {dl.PIPER_REPO, dl.VOSK_TTS_RU, dl.KOKORO_BASE.split("/releases/")[0].split("github.com/")[1]}
    ids |= set(dl.KOKORO_FILES) | set(dl.SPEAKER_FILES) | set(dl.SPEAKER_FALLBACK)
    ids |= {dl.piper_ru_dir(v).replace("piper-", "").replace("-medium", "") for v in dl.PIPER_RU}
    return ids


def test_every_downloadable_model_is_in_notices():
    assert sorted(i for i in _ids() if i not in NOTICES) == []


def test_vosk_tts_ru_marked_unknown_and_not_default():
    row = next(l for l in NOTICES.splitlines() if dl.VOSK_TTS_RU in l)
    assert "unknown" in row.lower()
    assert "opt-in" in NOTICES


def test_hymt_copyright_and_apache_text_shipped():
    assert "Copyright (C) 2026 Tencent. All rights reserved." in NOTICES
    lic = (ROOT / "LICENSES" / "Apache-2.0.txt").read_text("utf-8")
    assert "Apache License" in lic and "Version 2.0" in lic


def test_readme_links_notices_and_file_exists():
    readme = (ROOT / "README.md").read_text("utf-8")
    assert "](THIRD_PARTY_NOTICES.md)" in readme


def test_setup_prints_notices_pointer(monkeypatch, capsys, tmp_path):
    from tinglongwu_setup import cli
    monkeypatch.setattr(dl, "main", lambda argv: 0)
    assert cli.main(["--models-dir", str(tmp_path), "--hymt"]) == 0
    assert "THIRD_PARTY_NOTICES.md" in capsys.readouterr().out
