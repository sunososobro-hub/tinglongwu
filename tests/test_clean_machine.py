"""#9: packaged exe on a clean Windows - static DLL check, Windows Sandbox config, `tlwu.exe --smoke` (no network)."""
import importlib.util
import io
import struct
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "packaging"))
import clean_machine_check as cmc  # noqa: E402

from tlwu_app import main as app_main  # noqa: E402
from tlwu_app import smoke  # noqa: E402


def make_pe(imports, pe32plus=True):
    """Minimal PE file whose import table lists `imports` (enough for the loader's import walk)."""
    opt_size = (112 if pe32plus else 96) + 16 * 8
    sec_va, sec_raw = 0x1000, 0x400
    body = bytearray(20 * (len(imports) + 1))
    for i, name in enumerate(imports):
        struct.pack_into("<5I", body, 20 * i, 0, 0, 0, sec_va + len(body), 0)
        body += name.encode() + b"\0"
    f = bytearray(sec_raw)
    f[0:2] = b"MZ"
    struct.pack_into("<I", f, 0x3C, 0x40)
    f[0x40:0x44] = b"PE\0\0"
    struct.pack_into("<HHIIIHH", f, 0x44, 0x8664, 1, 0, 0, 0, opt_size, 0x22)
    opt = 0x58
    struct.pack_into("<H", f, opt, 0x20B if pe32plus else 0x10B)
    struct.pack_into("<II", f, opt + (112 if pe32plus else 96) + 8, sec_va, len(body))
    struct.pack_into("<8sIIII", f, opt + opt_size, b".idata", len(body), sec_va, len(body), sec_raw)
    return bytes(f + body)


@pytest.mark.parametrize("pe32plus", [True, False])
def test_pe_imports_reads_the_import_table(pe32plus):
    names = ["KERNEL32.dll", "VCRUNTIME140.dll", "python312.dll"]
    assert cmc.pe_imports(make_pe(names, pe32plus)) == names
    assert cmc.pe_imports(make_pe([])) == []


def test_pe_imports_ignores_non_pe_and_truncated_files():
    assert cmc.pe_imports(b"") == []
    assert cmc.pe_imports(b"not a pe file at all" * 10) == []
    assert cmc.pe_imports(make_pe(["a.dll"])[:0x60]) == []


def build_app(tmp_path, files):
    app = tmp_path / "tlwu"
    for rel, imports in files.items():
        p = app / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(make_pe(imports) if imports is not None else b"text")
    return app


def test_deps_flags_vc_runtime_missing_on_a_clean_machine(tmp_path, capsys):
    app = build_app(tmp_path, {
        "tlwu.exe": ["KERNEL32.dll", "python312.dll", "api-ms-win-crt-runtime-l1-1-0.dll"],
        "python312.dll": ["KERNEL32.dll", "VCRUNTIME140.dll"],
        "ctranslate2/ctranslate2.dll": ["MSVCP140.dll", "cublas64_12.dll", "nvcuda.dll"],
        "cuda_libs/cublas64_12.dll": ["KERNEL32.dll"],
        "readme.txt": None,
    })
    missing, driver = cmc.missing_dlls(app)
    assert set(missing) == {"vcruntime140.dll", "msvcp140.dll"}
    assert missing["msvcp140.dll"] == [str(Path("ctranslate2/ctranslate2.dll"))]
    assert set(driver) == {"nvcuda.dll"}
    assert cmc.main(["deps", str(app)]) == 1
    out = capsys.readouterr().out
    assert "MISSING on a clean Windows: vcruntime140.dll" in out and "deps: FAIL" in out


def test_deps_ok_when_runtime_is_shipped(tmp_path, capsys):
    app = build_app(tmp_path, {
        "tlwu.exe": ["KERNEL32.dll", "python312.dll"],
        "python312.dll": ["VCRUNTIME140.dll", "USER32.dll"],
        "vcruntime140.dll": ["KERNEL32.dll"],
    })
    assert cmc.main(["deps", str(app)]) == 0
    assert "deps: OK" in capsys.readouterr().out
    assert cmc.main(["deps", str(tmp_path / "nope")]) == 1


def test_sandbox_config_is_offline_readonly_and_runs_smoke(tmp_path):
    app = build_app(tmp_path, {"tlwu.exe": ["KERNEL32.dll"]})
    out = tmp_path / "check"
    out.mkdir()
    (out / "exitcode.txt").write_text("0")  # stale result from an earlier run must not count
    assert cmc.main(["sandbox", str(app), "--out", str(out)]) == 0
    assert cmc.read_result(out) is None
    cfg = ET.parse(out / "tlwu-clean.wsb").getroot()
    assert cfg.findtext("Networking") == "Disable" and cfg.findtext("VGpu") == "Disable"
    maps = {m.findtext("SandboxFolder"): m for m in cfg.iter("MappedFolder")}
    assert maps["C:\\tlwu"].findtext("HostFolder") == str(app.resolve())
    assert maps["C:\\tlwu"].findtext("ReadOnly") == "true"
    assert maps["C:\\tlwu-check"].findtext("ReadOnly") == "false"
    assert "C:\\tlwu-check\\run.ps1" in cfg.findtext("LogonCommand/Command")
    ps1 = (out / "run.ps1").read_bytes().decode()
    assert "tlwu.exe" in ps1 and "'--smoke'" in ps1 and "exitcode.txt" in ps1 and "\r\n" in ps1


def test_sandbox_run_waits_for_the_result(tmp_path, monkeypatch, capsys):
    app = build_app(tmp_path, {"tlwu.exe": ["KERNEL32.dll"]})
    out = tmp_path / "check"
    started = []

    def fake_popen(cmd):  # the sandbox "runs": smoke failed on a missing DLL
        started.append(cmd)
        (out / "smoke.txt").write_text("FAIL ctranslate2: ImportError: DLL load failed\n")
        (out / "exitcode.txt").write_text("\ufeff1\r\n", "utf-8")

    monkeypatch.setattr(cmc.subprocess, "Popen", fake_popen)
    assert cmc.cmd_sandbox(app, out, run=True, timeout=5, poll=0) == 1
    assert started[0][0] == "WindowsSandbox.exe" and started[0][1].endswith("tlwu-clean.wsb")
    text = capsys.readouterr().out
    assert "DLL load failed" in text and "sandbox: FAIL (tlwu.exe --smoke exit 1)" in text


def test_sandbox_run_times_out(tmp_path, monkeypatch, capsys):
    app = build_app(tmp_path, {"tlwu.exe": ["KERNEL32.dll"]})
    monkeypatch.setattr(cmc.subprocess, "Popen", lambda cmd: None)
    assert cmc.cmd_sandbox(app, tmp_path / "c", run=True, timeout=0, poll=0) == 1
    assert "TIMEOUT" in capsys.readouterr().out


def test_smoke_reports_each_module_and_fails_only_on_required():
    out = io.StringIO()
    assert smoke.check([("json", True), ("tlwu_no_such_mod", False)], out) == 0
    lines = out.getvalue().splitlines()
    assert lines[0].startswith("OK   json") and lines[1].startswith("WARN tlwu_no_such_mod: ModuleNotFoundError")
    out = io.StringIO()
    assert smoke.check([("tlwu_no_such_mod", True), ("json", True)], out) == 1
    assert out.getvalue().splitlines()[-1] == "smoke: FAIL (1 required module(s) failed)"


def test_smoke_survives_a_module_that_raises_oserror(tmp_path, monkeypatch):
    (tmp_path / "tlwu_bad_dll.py").write_text("raise OSError('[WinError 126] The specified module could not be found')")
    monkeypatch.syspath_prepend(str(tmp_path))
    out = io.StringIO()
    assert smoke.check([("tlwu_bad_dll", True)], out) == 1
    assert "FAIL tlwu_bad_dll: OSError: [WinError 126]" in out.getvalue()


def test_exe_smoke_flag_runs_the_check_without_models_or_gui(monkeypatch):
    calls = []
    monkeypatch.setattr(smoke, "check", lambda: calls.append(1) or 0)
    monkeypatch.setattr(app_main, "run_gui", lambda *a: pytest.fail("--smoke must not open the GUI"))
    assert app_main.main(["--smoke"]) == 0 and calls == [1]


def test_smoke_module_list_imports_here_except_windows_and_optional():
    """Every required module name is real (a typo would fail on every machine, not only clean ones)."""
    skip = {"soundcard", "llama_cpp"}  # soundcard needs a sound server; llama_cpp is an optional extra
    if importlib.util.find_spec("tkinter") is None:  # some Linux Pythons ship without Tk (the exe always has it)
        skip |= {"tkinter", "sv_ttk", "tinglongwu.gui.app"}
    names = [(n, r) for n, r in smoke.MODULES if n not in skip]
    out = io.StringIO()
    assert smoke.check(names, out) == 0, out.getvalue()
