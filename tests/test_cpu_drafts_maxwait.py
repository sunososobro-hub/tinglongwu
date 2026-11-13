"""#60: the menus offer live drafts (--stream), and on CPU an unfinished sentence is held at most CPU_MAX_WAIT s
unless the user chose --preset / --max-wait.

The PowerShell test needs `pwsh` (preinstalled on the GitHub ubuntu runners, where CI runs pytest)."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from tinglongwu import cli, device

ROOT = Path(__file__).resolve().parent.parent
PWSH = shutil.which("pwsh")


# ---------------------------------------------------------------- CLI: max-wait on CPU

class _Stop(Exception):
    pass


def _session_max_wait(monkeypatch, tmp_path, argv, asr_device):
    """Run run_session up to the sentence merger and return the max_wait it is built with, plus stderr notes."""
    import tinglongwu.asr as asr
    import tinglongwu.merge as merge

    monkeypatch.setattr(device, "cuda_device_count", lambda: 0)
    monkeypatch.setattr(device, "driver_version", lambda: None)
    monkeypatch.setattr(asr, "FasterWhisperRecognizer", lambda *a, **k: SimpleNamespace(device=asr_device))
    monkeypatch.setattr(cli, "make_translator", lambda *a, **k: SimpleNamespace(preload=lambda *x: None))
    monkeypatch.setattr(cli, "select_backend", lambda *a: object())

    def merger(max_wait, **kw):
        raise _Stop(max_wait)

    monkeypatch.setattr(merge, "SentenceMerger", merger)
    a = cli.parse_args(["--models-dir", str(tmp_path), "--model", "small", "--lang", "en", "--no-adapt", *argv])
    with pytest.raises(_Stop) as e:
        cli.run_session(a)
    return e.value.args[0]


def test_cpu_default_uses_shorter_max_wait(monkeypatch, tmp_path, capsys):
    assert _session_max_wait(monkeypatch, tmp_path, ["--device", "cpu"], "cpu") == cli.CPU_MAX_WAIT == 3.0
    assert "at most 3 s" in capsys.readouterr().err


def test_gpu_fell_back_to_cpu_also_shortens(monkeypatch, tmp_path):
    # auto with no usable GPU resolves to cpu: same as an explicit --device cpu
    assert _session_max_wait(monkeypatch, tmp_path, [], "cpu") == 3.0


@pytest.mark.parametrize("argv, want", [
    (["--max-wait", "6"], 6.0),           # explicit value is kept, even the old default
    (["--max-wait", "5"], 5.0),
    (["--preset", "meeting"], 6.0),       # an explicit preset is a choice, too
    (["--preset", "chat"], 1.5),          # already below the CPU value: never raised
])
def test_cpu_explicit_choice_is_kept(monkeypatch, tmp_path, capsys, argv, want):
    assert _session_max_wait(monkeypatch, tmp_path, ["--device", "cpu", *argv], "cpu") == want
    assert "at most" not in capsys.readouterr().err


def test_cuda_keeps_preset_max_wait(monkeypatch, tmp_path, capsys):
    assert _session_max_wait(monkeypatch, tmp_path, [], "cuda") == 6.0
    assert "at most" not in capsys.readouterr().err


def test_parse_args_defaults_unchanged_and_help_mentions_cpu(capsys):
    a = cli.parse_args([])
    assert (a.preset, a.chunk, a.max_wait, a.max_wait_default) == ("meeting", 8.0, 6.0, True)
    assert cli.parse_args(["--preset", "meeting"]).max_wait_default is False
    assert cli.parse_args(["--max-wait", "6"]).max_wait_default is False
    with pytest.raises(SystemExit):
        cli.parse_args(["--help"])
    assert "On CPU without --preset / --max-wait the default max-wait is 3 s" in " ".join(capsys.readouterr().out.split())


# ---------------------------------------------------------------- Linux/macOS menu: live drafts choice

def _fake_menu(tmp_path: Path) -> tuple[Path, Path]:
    """A copy of scripts/ next to a fake .venv: python always succeeds (models present), tlwu records its args."""
    root = tmp_path / "repo"
    shutil.copytree(ROOT / "scripts", root / "scripts")
    bin_ = root / ".venv" / "bin"
    bin_.mkdir(parents=True)
    log = tmp_path / "tlwu_args"
    (bin_ / "activate").write_text(f'export PATH="{bin_}:$PATH"\n')
    (bin_ / "python").write_text('#!/usr/bin/env bash\ncase "$*" in *netreport*) exit 1 ;; esac\n'
                                 '[ "${1:-}" = "-" ] && cat >/dev/null\n'
                                 'exit 0\n')
    (bin_ / "tlwu").write_text(f'#!/usr/bin/env bash\nprintf "%s\\n" "$@" > "{log}"\n')
    for f in bin_.iterdir():
        f.chmod(0o755)
    return root, log


def _run_menu(tmp_path, answers: str):
    root, log = _fake_menu(tmp_path)
    env = {**os.environ, "TLWU_NO_UPDATE": "1", "TLWU_UI_LANG": "en", "TLWU_LANG": "en",
           "TLWU_CONFIG_DIR": str(tmp_path / "cfg"), "HOME": str(tmp_path)}
    r = subprocess.run(["bash", str(root / "scripts" / "linux_menu.sh")], input=answers, capture_output=True,
                       text=True, env=env, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    return log.read_text().split("\n")[:-1], r.stdout


def test_linux_menu_listen_drafts_yes_passes_stream(tmp_path):
    # 3 = listen only; Enter = do not save; y = live drafts
    args, _ = _run_menu(tmp_path, "3\n\ny\n")
    assert args == ["--lang", "en", "--stream"]


@pytest.mark.parametrize("answers", ["3\n\n\n", "3\n\nn\n", "3\n\n"])  # Enter, n, and stdin closed = no
def test_linux_menu_listen_drafts_default_off(tmp_path, answers):
    args, _ = _run_menu(tmp_path, answers)
    assert args == ["--lang", "en"]


def test_linux_menu_save_and_drafts_together(tmp_path):
    args, _ = _run_menu(tmp_path, "3\ny\nyes\n")
    assert args == ["--lang", "en", "--save", "--stream"]


@pytest.mark.parametrize("lang", ["en", "ru", "zh-TW"])
def test_drafts_prompt_translated_in_every_menu_language(lang):
    r = subprocess.run(["bash", "-c", f'. "{ROOT}/scripts/i18n/en.sh"; en="$M_ASK_STREAM"; '
                                      f'. "{ROOT}/scripts/i18n/{lang}.sh"; printf "%s\\n%s" "$en" "$M_ASK_STREAM"'],
                       capture_output=True, text=True, encoding="utf-8")
    en, msg = r.stdout.split("\n")
    assert "y=" in msg and (lang == "en" or msg != en)


# ---------------------------------------------------------------- Windows menu: live drafts choice

@pytest.mark.parametrize("ui", ["en", "ru", "zh-TW"])
@pytest.mark.parametrize("dflt, answer, want", [
    ("0", "y", "--stream"), ("0", "yes", "--stream"), ("0", "", ""), ("0", "n", ""),      # GPU / unknown: default off
    ("1", "", "--stream"), ("1", "y", "--stream"), ("1", "n", ""), ("1", "no", "")])      # #257 CPU listen-only: on
def test_windows_menu_drafts_question(tmp_path, ui, dflt, answer, want):
    assert PWSH, "pwsh is needed for the Windows menu test (preinstalled on GitHub ubuntu runners)"
    common = ROOT / "scripts" / "tlwu_common.ps1"
    script = (f". '{common}'\n"
              "Import-TlwuMessages\n"
              # a function shadows the cmdlet: answer the question without a console
              f"function Read-Host {{ param($p) Write-Host ('PROMPT=' + $p); return '{answer}' }}\n"
              # stub for `python -m tinglongwu.menuopts stream-default listen` (its logic is unit-tested separately)
              f"function fakepy {{ '{dflt}' }}\n"
              "$s = @(Get-TlwuStreamArgs fakepy)\n"
              "Write-Host ('COUNT=' + $s.Count)\n"
              "Write-Host ('ARGS=' + ($s -join ' '))\n")
    env = {**os.environ, "POWERSHELL_TELEMETRY_OPTOUT": "1", "POWERSHELL_UPDATECHECK": "Off",
           "TLWU_UI_LANG": ui, "TLWU_CONFIG_DIR": str(tmp_path)}
    r = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True,
                       text=True, encoding="utf-8", timeout=120, env=env, cwd=str(ROOT))
    assert r.returncode == 0, r.stderr
    lines = r.stdout.splitlines()
    assert f"ARGS={want}" in lines and f"COUNT={1 if want else 0}" in lines
    prompt = next(x for x in lines if x.startswith("PROMPT="))
    assert ("n=" if dflt == "1" else "y=") in prompt and "ASK_STREAM" not in prompt
