"""#767: `build_installer.py --core-dir DIR` builds a wizard for the prebuilt core (tlwu.exe), no install.ps1 / pip."""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("build_installer", ROOT / "installer" / "build_installer.py")
bi = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bi)


def _core(tmp_path: Path) -> Path:
    d = tmp_path / "dist_tlwu"
    (d / "llama_cpp" / "lib").mkdir(parents=True)
    (d / "tlwu.exe").write_bytes(b"MZ")
    (d / "python312.dll").write_bytes(b"x")
    (d / "llama_cpp" / "lib" / "llama.dll").write_bytes(b"x")
    return d


def _section(iss: str, name: str) -> list[str]:
    m = re.search(rf"^\[{name}\]\n(.*?)(?=^\[|\Z)", iss, re.M | re.S)
    return [ln for ln in (m.group(1) if m else "").splitlines() if ln.strip() and not ln.startswith(";")]


def _dry_run(tmp_path, monkeypatch, capsys, *extra):
    monkeypatch.setattr(bi, "find_iscc", lambda: None)
    out = tmp_path / "out"
    assert bi.main(["--dry-run", "--out", str(out), *extra]) == 0
    cmd_line = capsys.readouterr().out.strip().splitlines()[-1]
    return out, cmd_line.split()


def test_core_dir_stages_exactly_the_core_files_plus_license(tmp_path, monkeypatch, capsys):
    out, cmd = _dry_run(tmp_path, monkeypatch, capsys, "--core-dir", str(_core(tmp_path)))
    stage = out / "stage"
    files = sorted(p.relative_to(stage).as_posix() for p in stage.rglob("*") if p.is_file())
    assert files == ["LICENSE", "llama_cpp/lib/llama.dll", "python312.dll", "tlwu.exe"]
    assert (out / "tinglongwu.ico").read_bytes() == bi.APP_ICON.read_bytes()
    for bad in ("install.ps1", "install.bat", "requirements.txt", ".venv", "installer/run_install.ps1"):
        assert not (stage / bad).exists(), bad
    assert "/DCoreLayout=1" in cmd


def test_core_wizard_shortcuts_and_run_start_tlwu_exe(tmp_path, monkeypatch, capsys):
    _, cmd = _dry_run(tmp_path, monkeypatch, capsys, "--core-dir", str(_core(tmp_path)))
    iss = bi.effective_iss(cmd)
    icons, run = _section(iss, "Icons"), _section(iss, "Run")
    assert icons and all('Filename: "{app}\\tlwu.exe"' in ln for ln in icons)
    assert run and all('Filename: "{app}\\tlwu.exe"' in ln for ln in run)
    assert "repair install" not in "\n".join(icons)
    assert not any(".venv" in ln or "install.bat" in ln for ln in icons + run)
    # install.ps1 is never started, and the finish page does not report a failed install.ps1 run
    code = iss.split("[Code]", 1)[1]
    post = code.split("procedure CurStepChanged", 1)[1].split("end;\n\nprocedure", 1)[0]
    assert "RunInstall;" not in post and "InstallRC := 0;" in post
    assert bi.missing_defines(bi.ISS.read_text("utf-8-sig"), cmd) == []


def test_without_core_dir_the_source_wizard_is_unchanged(tmp_path):
    cmd = bi.iscc_cmd("ISCC.exe", tmp_path / "stage", tmp_path / "i.ico", tmp_path, "1.2.3")
    assert not any(a.startswith("/DCoreLayout") for a in cmd)
    iss = bi.effective_iss(cmd)
    icons = _section(iss, "Icons")
    assert any(".venv\\Scripts\\tlwu-gui.exe" in ln for ln in icons)
    assert any("repair install" in ln for ln in icons)
    assert "tlwu.exe" not in "\n".join(icons + _section(iss, "Run"))
    post = iss.split("procedure CurStepChanged", 1)[1].split("end;\n\nprocedure", 1)[0]
    assert "RunInstall;" in post and "InstallRC := 0;" not in post
    assert "ShouldSkipPage" not in iss


def test_core_layout_keeps_uninstall_keep_models_logic(tmp_path):
    src = bi.effective_iss(bi.iscc_cmd("I", tmp_path, tmp_path / "i", tmp_path, "1"))
    core = bi.effective_iss(bi.iscc_cmd("I", tmp_path, tmp_path / "i", tmp_path, "1", core=True))
    for name in ("CurUninstallStepChanged", "DeleteLeftovers"):
        body = lambda s: s.split(f"procedure {name}", 1)[1].split("\nend;", 1)[0]  # noqa: E731
        assert body(core) == body(src)  # models\ kept on "Yes", everything else (incl. tlwu.exe) removed
    assert _section(core, "UninstallDelete") == _section(src, "UninstallDelete")


def test_output_name_and_disk_constant(tmp_path):
    core = bi.iscc_cmd("I", tmp_path, tmp_path / "i", tmp_path, "9.8.7", core=True)
    assert f"/DExtraDiskBytes={bi.EXTRA_DISK_BYTES_CORE}" in core
    assert bi.EXTRA_DISK_BYTES_CORE < bi.EXTRA_DISK_BYTES  # no Python / pip packages to install
    iss = bi.effective_iss(core)
    assert "OutputBaseFilename=tinglongwu-setup-{#AppVersion}" in iss  # auto-update (#698) looks for this name


def test_core_dir_without_tlwu_exe_fails(tmp_path):
    d = tmp_path / "empty"
    d.mkdir()
    with pytest.raises(SystemExit, match="tlwu.exe not found"):
        bi.main(["--dry-run", "--out", str(tmp_path / "o"), "--core-dir", str(d)])


def test_preprocess_branches_and_nesting():
    text = "a\n#ifdef X\nb\n#ifndef Y\nc\n#else\nd\n#endif\n#else\ne\n#endif\nf\n"
    assert bi.preprocess(text, set()).split() == ["a", "e", "f"]
    assert bi.preprocess(text, {"X"}).split() == ["a", "b", "c", "f"]
    assert bi.preprocess(text, {"X", "Y"}).split() == ["a", "b", "d", "f"]
    with pytest.raises(ValueError):
        bi.preprocess("#ifdef X\na\n", set())
