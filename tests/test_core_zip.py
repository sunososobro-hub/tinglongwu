import subprocess
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import release_check  # noqa: E402


def _zip(path, names, pad=0):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as z:
        for n in names:
            z.writestr(n, b"x")
        if pad:
            z.writestr("tlwu/pad.bin", b"\0" * pad)
    return path


def test_clean_core_zip_passes(tmp_path):
    z = _zip(tmp_path / "core.zip", ["tlwu/tlwu.exe", "tlwu/llama.dll"])
    assert release_check.core_zip_problems(z) == []
    assert release_check.main([str(z)]) == 0


def test_cuda_files_rejected(tmp_path):
    z = _zip(tmp_path / "c.zip", ["tlwu/tlwu.exe", "tlwu/cuda_libs/cublas64_12.dll", "tlwu/ggml-cuda.dll"])
    probs = release_check.core_zip_problems(z)
    assert len(probs) == 2 and release_check.main([str(z)]) == 1


def test_oversize_exits_nonzero_and_prints_size(tmp_path):
    z = _zip(tmp_path / "big.zip", ["tlwu/tlwu.exe"], pad=2 << 20)
    r = subprocess.run([sys.executable, str(REPO / "scripts" / "release_check.py"), str(z), "--max-mb", "1"],
                       capture_output=True, text=True)
    assert r.returncode == 1 and "MB" in r.stdout and "over the 1 MB budget" in r.stdout


def test_build_script_accepts_core_flag():
    src = (REPO / "packaging" / "build_windows.py")
    r = subprocess.run([sys.executable, str(src), "--help"], capture_output=True, text=True)
    assert "--core" in r.stdout


def test_windows_build_workflow_runs_core_zip_check_after_lite_build():
    import yaml

    wf = yaml.safe_load((REPO / ".github" / "workflows" / "windows-build.yml").read_text(encoding="utf-8"))
    steps = [s for job in wf["jobs"].values() for s in job.get("steps", [])]
    runs = [str(s.get("run", "")) for s in steps]
    build = next(i for i, r in enumerate(runs) if "build_windows.py" in r and "--lite" in r)
    check = next(i for i, r in enumerate(runs) if "release_check.py" in r)
    assert check > build
    assert "tinglongwu-win64.zip" in runs[check] and "exit $LASTEXITCODE" in runs[check]
    assert steps[check]["if"] == steps[build]["if"]
