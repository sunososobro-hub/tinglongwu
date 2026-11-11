"""#124: one-click talk mode setup (tlwu-setup --talk): pinned Kokoro files, own venv, smoke wav, launcher."""
import hashlib
import io
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tinglongwu_setup import cli, downloader, talk

ONNX, VOICES = b"onnx-model-bytes" * 10, b"voices-bytes" * 7


@pytest.fixture
def pins(monkeypatch):
    files = {"kokoro-v1.0.int8.onnx": (len(ONNX), hashlib.sha256(ONNX).hexdigest()),
             "voices-v1.0.bin": (len(VOICES), hashlib.sha256(VOICES).hexdigest())}
    monkeypatch.setattr(downloader, "KOKORO_FILES", files)
    monkeypatch.setattr(downloader, "KOKORO_PINS", {n: h for n, (_, h) in files.items()})


class Opener:
    def __init__(self, data=None):
        self.data = data or {"kokoro-v1.0.int8.onnx": ONNX, "voices-v1.0.bin": VOICES}
        self.urls = []

    def __call__(self, url, timeout=60):
        self.urls.append(url)
        return io.BytesIO(self.data[url.rsplit("/", 1)[1]])


def test_real_pins_match_the_issue():
    assert downloader.KOKORO_FILES["kokoro-v1.0.int8.onnx"] == (
        92361271, "6e742170d309016e5891a994e1ce1559c702a2ccd0075e67ef7157974f6406cb")
    assert downloader.KOKORO_FILES["voices-v1.0.bin"] == (
        28214398, "bca610b8308e8d99f32efe4197e7ec01679264efed0cac9140fe9c29f1fbf7d")


def test_get_kokoro_downloads_pins_and_skips_on_rerun(tmp_path, pins):
    man, op = {}, Opener()
    downloader.get_kokoro(man, tmp_path, op)
    assert (tmp_path / "kokoro" / "kokoro-v1.0.int8.onnx").read_bytes() == ONNX
    assert len(op.urls) == 2 and all(u.startswith(downloader.KOKORO_BASE) for u in op.urls)
    assert set(man["kokoro"]["files"]) == {"kokoro-v1.0.int8.onnx", "voices-v1.0.bin"}
    op2 = Opener()
    downloader.get_kokoro(man, tmp_path, op2)  # re-run: nothing downloaded
    assert op2.urls == []


def test_get_kokoro_bad_download_is_deleted_and_aborts(tmp_path, pins):
    op = Opener({"kokoro-v1.0.int8.onnx": b"x" * len(ONNX), "voices-v1.0.bin": VOICES})
    with pytest.raises(SystemExit, match="SHA256 MISMATCH"):
        downloader.get_kokoro({}, tmp_path, op)
    assert list((tmp_path / "kokoro").iterdir()) == []


def test_get_kokoro_redownloads_corrupt_local_file(tmp_path, pins):
    downloader.get_kokoro({}, tmp_path, Opener())
    (tmp_path / "kokoro" / "voices-v1.0.bin").write_bytes(b"broken")
    op = Opener()
    downloader.get_kokoro({}, tmp_path, op)
    assert [u.rsplit("/", 1)[1] for u in op.urls] == ["voices-v1.0.bin"]
    assert (tmp_path / "kokoro" / "voices-v1.0.bin").read_bytes() == VOICES


def test_verify_offline_checks_kokoro_against_pins(tmp_path, pins):
    man = {}
    downloader.get_kokoro(man, tmp_path, Opener())
    (tmp_path / "manifest.json").write_text(json.dumps(man), "utf-8")
    assert downloader.main(["--verify", "--models-dir", str(tmp_path)]) == 0
    (tmp_path / "kokoro" / "voices-v1.0.bin").write_bytes(b"tampered")
    with pytest.raises(SystemExit, match="MISMATCH"):
        downloader.main(["--verify", "--models-dir", str(tmp_path)])


class Runner:
    def __init__(self, imports_ok=False):
        self.cmds, self.imports_ok = [], imports_ok

    def __call__(self, cmd):
        cmd = [str(c) for c in cmd]
        self.cmds.append(cmd)
        if cmd[1:3] == ["-m", "venv"]:
            py = talk.venv_python(Path(cmd[3]))
            py.parent.mkdir(parents=True)
            py.write_text("")
            return 0
        if cmd[1] == "-c" and cmd[2].startswith("import kokoro_onnx"):
            return 0 if self.imports_ok else 1
        if cmd[1:3] == ["-m", "pip"]:
            self.imports_ok = True
            return 0
        if cmd[1] == "-c" and cmd[2] == talk.SMOKE_CODE:
            Path(cmd[4]).write_bytes(b"RIFF....")
            return 0
        return 1


def test_ensure_venv_creates_and_installs_binary_only_then_skips(tmp_path):
    r = Runner()
    py = talk.ensure_venv(tmp_path / "tts-kokoro", r, base="base-python")
    assert r.cmds[0] == ["base-python", "-m", "venv", str(tmp_path / "tts-kokoro")]
    pip = [c for c in r.cmds if c[1:3] == ["-m", "pip"]]
    assert pip == [[str(py), "-m", "pip", "install", "--only-binary=:all:", "kokoro-onnx==0.6.1", "soundfile", "numpy"]]
    r2 = Runner(imports_ok=True)
    talk.ensure_venv(tmp_path / "tts-kokoro", r2, base="base-python")
    assert [c for c in r2.cmds if "venv" in c or "pip" in c] == []


def test_ensure_venv_pip_failure_aborts(tmp_path):
    def run(cmd):
        return 0 if "venv" in [str(c) for c in cmd] and not talk.venv_python(tmp_path / "v").parent.mkdir(
            parents=True) and not talk.venv_python(tmp_path / "v").write_text("") else 1
    with pytest.raises(SystemExit, match="pip install kokoro-onnx failed"):
        talk.ensure_venv(tmp_path / "v", run, base="b")


def test_launcher_auto_mic_tier_args_and_quoting(tmp_path):
    s = talk.render_launcher(Path("C:/Users/o'neil/tlwu"), Path("C:/k/python.exe"), Path("C:/m/kokoro"),
                             "large-v3-turbo", "cuda", "auto")
    run_line = next(x for x in s.splitlines() if x.startswith("$a = @("))
    args = [a.strip().strip("'") for a in run_line[len("$a = @("):-1].split("', '")]
    assert args[args.index("--mic") + 1] == "--compute"  # no microphone name written
    assert args[args.index("--whisper") + 1] == "large-v3-turbo" and args[args.index("--mt") + 1] == "auto"
    assert args[args.index("--qwen") + 1] == "qwen3-4b" and args[args.index("--device") + 1] == "CABLE Input"
    assert "Set-Location 'C:/Users/o''neil/tlwu'" in s.replace("\\", "/")
    assert s.splitlines()[2].startswith("param(")  # param() must be the first statement
    assert "--qwen" not in talk.render_launcher(Path("r"), Path("p"), Path("m"), "small", "cpu", "opus")


def test_cli_talk_end_to_end(tmp_path, pins, monkeypatch):
    pairs = []
    monkeypatch.setattr(downloader, "_open", Opener())
    monkeypatch.setattr(downloader.get_kokoro, "__defaults__", (Opener(),))
    monkeypatch.setattr(downloader, "get_pair", lambda man, mdir, pr: pairs.append(pr))
    r = Runner()
    monkeypatch.setattr(talk.subprocess, "call", r)
    monkeypatch.setattr(talk.main, "__defaults__", (r,))
    launcher = tmp_path / "tlwu-talk.ps1"
    rc = cli.main(["--talk", "--models-dir", str(tmp_path / "models"), "--talk-venv", str(tmp_path / "venv"),
                   "--talk-launcher", str(launcher), "--talk-whisper", "small", "--talk-compute", "cpu"])
    assert rc == 0 and pairs == ["zh-en"]
    assert (tmp_path / "models" / "kokoro-smoke.wav").is_file()
    assert "kokoro" in json.loads((tmp_path / "models" / "manifest.json").read_text("utf-8"))
    assert launcher.read_bytes().startswith(b"\xef\xbb\xbf")  # BOM for PowerShell 5.1
    text = launcher.read_text("utf-8-sig")
    assert str(talk.venv_python(tmp_path / "venv")) in text and "--qwen" not in text


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="pwsh not installed (runs on CI ubuntu)")
def test_install_dry_run_sets_up_talk_mode_and_both_shortcuts():
    # #124: install.ps1 step 6 runs `tlwu-setup --talk` with the tier's args, step 7 makes both desktop shortcuts
    root = Path(__file__).resolve().parent.parent
    r = subprocess.run(["pwsh", "-NoProfile", "-NonInteractive", "-Command",
                        f"& '{root / 'install.ps1'}' -DryRun -Cpu; exit $LASTEXITCODE"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    lines = r.stdout.splitlines()
    if r.returncode != 0 and any("Python 3.10-3.12 not found" in x for x in lines):
        pytest.skip("no Python 3.10-3.12 on PATH for the installer's step 1")
    assert r.returncode == 0, r.stdout + r.stderr
    s6 = next(i for i, x in enumerate(lines) if x.startswith("[6/8] "))
    s7 = next(i for i, x in enumerate(lines) if x.startswith("[7/8] "))
    s8 = next(i for i, x in enumerate(lines) if x.startswith("[8/8] "))
    talk = [x for x in lines[s6:s7] if "[dry-run]" in x and "--talk" in x]
    assert len(talk) == 1 and talk[0].rstrip().endswith("--talk --talk-whisper small --talk-compute cpu --talk-mt opus")
    lnk = [x for x in lines[s7:s8] if "[dry-run] shortcut:" in x]
    assert len(lnk) == 2 and "聽攏有 說話模式.lnk" in lnk[1] and "tlwu-talk.ps1" in lnk[1]
