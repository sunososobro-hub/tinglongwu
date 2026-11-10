"""The Vosk-TTS Russian voice model has no stated upstream license: opt-in only, never in a default setup."""
import io
import re
import zipfile
from pathlib import Path

import pytest

from tinglongwu_setup import cli, downloader as dl

ROOT = Path(__file__).resolve().parent.parent
LICENSE_EN = "Russian voice model license is not stated upstream; download at your own responsibility"


def test_default_setup_does_not_download_vosk(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise AssertionError("vosk model must not be fetched without --vosk-tts-ru")

    monkeypatch.setattr(dl, "get_vosk_tts_ru", boom)
    assert dl.main(["--models-dir", str(tmp_path)]) == 0
    assert dl.main(["--verify", "--models-dir", str(tmp_path)]) == 0
    assert cli.main(["--models-dir", str(tmp_path)]) == 0


def test_explicit_flag_is_the_only_trigger(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(dl, "get_vosk_tts_ru", lambda man, mdir: calls.append(mdir))
    assert cli.main(["--vosk-tts-ru", "--models-dir", str(tmp_path)]) == 0
    assert calls == [tmp_path]


def test_installers_never_mention_the_model():
    for f in ("install.ps1", "install.bat", "scripts/mac_install.sh"):
        txt = (ROOT / f).read_text("utf-8")
        for line in txt.splitlines():
            if ".venv/bin/tlwu-setup" in line or "tlwu-setup.exe" in line or "$VSet" in line:
                assert "vosk" not in line.lower(), (f, line)
        assert "vosk-model-tts-ru-0.7-multi.zip" not in txt, f


def test_menus_ask_before_download_default_no():
    sh = (ROOT / "scripts/linux_menu.sh").read_text("utf-8")
    ps = (ROOT / "scripts/windows_menu.ps1").read_text("utf-8-sig")
    for txt, lic, ask, setup in ((sh, "M_VOSK_LICENSE", "M_ASK_VOSK", "tlwu-setup --vosk-tts-ru"),
                                 (ps, "VOSK_LICENSE", "ASK_VOSK", "--vosk-tts-ru")):
        assert txt.index(lic) < txt.index(ask) < txt.index(setup, txt.index(ask))
        assert txt.count("--vosk-tts-ru") == 2 or "--vosk-tts-ru" in txt  # header comment + the gated call
    assert re.search(r"y\|Y\|yes", sh) and "vans" in sh and "-match '^(y|yes)$'" in ps


@pytest.mark.parametrize("lang", ["en", "ru", "zh-TW"])
def test_menu_i18n_has_vosk_keys(lang):
    sh = (ROOT / f"scripts/i18n/{lang}.sh").read_text("utf-8")
    ps = (ROOT / f"scripts/i18n/{lang}.psd1").read_text("utf-8-sig")
    for k in ("VOSK_LICENSE", "ASK_VOSK", "VOSK_OPTIN"):
        assert f"M_{k}=" in sh and re.search(rf"^\s*{k} = ", ps, re.M)
    assert "[y/N]" in sh and "tlwu-setup --vosk-tts-ru" in sh
    if lang == "en":
        assert LICENSE_EN in sh and LICENSE_EN in ps


def test_downloader_prints_license_and_extracts(monkeypatch, tmp_path, capsys):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("vosk-model-tts-ru-0.7-multi/model.onnx", b"x")
        z.writestr("vosk-model-tts-ru-0.7-multi/config.json", b"{}")

    def opener(url, timeout=0):
        assert url == dl.VOSK_TTS_RU_URL
        return io.BytesIO(buf.getvalue())

    monkeypatch.setenv("TLWU_UI_LANG", "en")
    man = {}
    dl.get_vosk_tts_ru(man, tmp_path, opener=opener)
    assert LICENSE_EN in capsys.readouterr().out
    assert (tmp_path / dl.VOSK_TTS_RU / "model.onnx").is_file() and dl.VOSK_TTS_RU in man


def test_missing_model_ru_speak_falls_back_to_subtitles(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("TLWU_UI_LANG", "en")
    from test_reverse_en import _parse

    a = _parse(["--to", "ru", "--tts-model", str(tmp_path / "nope")])
    err = capsys.readouterr().err
    assert a.no_tts is True and a.ru_voice is False
    assert "tlwu-setup --vosk-tts-ru" in err and "subtitles only" in err and LICENSE_EN in err


def test_present_model_keeps_tts(tmp_path):
    from test_reverse_en import _parse

    a = _parse(["--to", "ru", "--tts-model", str(tmp_path)])
    assert a.no_tts is False


def test_follow_without_ru_model_degrades(capsys, monkeypatch):
    monkeypatch.setenv("TLWU_UI_LANG", "en")
    from test_reverse_en import _parse
    from tinglongwu.reverse.proto import FollowTTS

    a = _parse(["--to", "follow", "--tts-engine", "kokoro", "--tts-model", "k"])
    assert a.ru_voice is False and "tlwu-setup --vosk-tts-ru" in capsys.readouterr().err
    f = FollowTTS({"en": lambda: (_ for _ in ()).throw(AssertionError("en must not load"))})
    assert list(f.speak("Привет")) == []  # Russian text: no crash, nothing spoken
