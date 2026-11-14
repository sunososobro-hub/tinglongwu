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


# ---- #762: release.yml `windows` job builds the slim core zip and gates it with the core budget

def _workflow(name):
    import yaml

    return yaml.safe_load((REPO / ".github" / "workflows" / name).read_text(encoding="utf-8"))


def _release_windows_steps():
    return _workflow("release.yml")["jobs"]["windows"]["steps"]


def _nuitka_cache(steps):
    return next(s for s in steps if s.get("name") == "Nuitka compile cache")["with"]


def test_release_windows_job_builds_core_from_the_cpu_llama_venv():
    runs = [str(s.get("run", "")) for s in _release_windows_steps()]
    text = "\n".join(runs)
    builds = [r for r in runs if "build_windows.py" in r]
    assert len(builds) == 1 and "build_windows.py --core" in builds[0] and "--allow-cpu-only" not in text
    venv = next(r for r in runs if "pip install -r" in r)
    assert "requirements-llama-cpu.txt --only-binary=:all:" in venv
    for gone in ("requirements-llama-cuda.txt", "requirements-gpu.txt", "nvrtc", "cudart64_12.dll", "cuda_libs"):
        assert gone not in text, gone


def test_release_windows_job_gates_core_budget_before_the_packaging_check():
    steps = _release_windows_steps()
    runs = [str(s.get("run", "")) for s in steps]
    build = next(i for i, r in enumerate(runs) if "build_windows.py" in r)
    core = next(i for i, r in enumerate(runs) if "scripts\\release_check.py" in r and "tinglongwu-win64.zip" in r)
    pack = next(i for i, r in enumerate(runs) if "packaging\\release_check.py" in r)
    upload = next(i for i, s in enumerate(steps) if "upload-artifact" in str(s.get("uses", "")))
    assert build < core < pack < upload
    for i in (core, pack):  # a failed check stops the job: no `if`, no continue-on-error, exit code passed on
        assert "exit $LASTEXITCODE" in runs[i] and "if" not in steps[i] and "continue-on-error" not in steps[i]
    assert "--max-mb" not in runs[core]  # the default budget (MAX_CORE_MB), not a number typed into the workflow
    assert release_check.MAX_CORE_MB == 300


def test_release_nuitka_cache_key_is_the_windows_build_key():
    rel = _nuitka_cache(_release_windows_steps())
    ci = _nuitka_cache(_workflow("windows-build.yml")["jobs"]["build"]["steps"])
    assert rel["key"] == ci["key"]
    assert "hashFiles('requirements.txt', 'requirements-llama-cpu.txt')" in rel["key"]
    ci_restore, rel_restore = ci["restore-keys"].split(), rel["restore-keys"].split()
    assert rel_restore[:len(ci_restore)] == ci_restore  # CI's keys first; extra fallbacks only after them
    assert "cuda" not in rel["key"] + rel["restore-keys"] and "requirements-gpu" not in rel["key"]


def test_release_windows_job_reports_time_without_gating():
    steps = _release_windows_steps()
    first = str(steps[0].get("run", ""))
    assert "TLWU_JOB_START=" in first  # taken in the first step, so the whole job is measured
    last = steps[-1]
    assert "release_check.py --job-start $env:TLWU_JOB_START" in last["run"]
    assert last["continue-on-error"] is True and "always()" in last["if"]


def test_job_time_line_within_and_over_budget():
    assert release_check.CORE_JOB_BUDGET_MIN == 20
    budget_s = release_check.CORE_JOB_BUDGET_MIN * release_check.SECONDS_PER_MIN
    ok, line = release_check.job_time_line(1000.0, 1000.0 + budget_s)
    assert ok and "20.0 min (within the 20 min budget)" in line
    ok, line = release_check.job_time_line(1000.0, 1000.0 + budget_s + release_check.SECONDS_PER_MIN)
    assert not ok and "21.0 min (OVER the 20 min budget)" in line
    assert release_check.job_time_line(1000.0, 900.0)[1].startswith("windows job time: 0.0 min")  # clock skew


def test_job_time_over_budget_warns_but_exits_zero(tmp_path, monkeypatch, capsys):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    assert release_check.main(["--job-start", "0"], now=25 * release_check.SECONDS_PER_MIN) == 0
    assert capsys.readouterr().out.startswith("::warning::windows job time: 25.0 min (OVER")
    assert release_check.main(["--job-start", "0"], now=12 * release_check.SECONDS_PER_MIN) == 0
    assert capsys.readouterr().out.startswith("windows job time: 12.0 min (within")
    assert summary.read_text("utf-8").count("windows job time") == 2


def test_core_check_writes_size_and_failures_to_the_job_summary(tmp_path, monkeypatch):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    assert release_check.main([str(_zip(tmp_path / "ok.zip", ["tlwu/tlwu.exe"]))]) == 0
    assert release_check.main([str(_zip(tmp_path / "c.zip", ["tlwu/cuda_libs/cudart64_12.dll"]))]) == 1
    text = summary.read_text("utf-8")
    assert text.count("core zip size: 0.0 MB (budget 300 MB)") == 2 and "FAIL: CUDA file in core zip" in text


def test_release_check_needs_a_zip_or_a_job_start(tmp_path):
    import pytest

    with pytest.raises(SystemExit) as e:
        release_check.main([])
    assert e.value.code == 2
    assert release_check.main([str(tmp_path / "missing.zip")]) == 2


def test_release_notes_say_the_zip_is_the_cuda_free_core():
    import release_notes

    for line in (release_notes.gpu_line("https://x/hw", "a" * 40), release_notes.gpu_line("", "")):
        assert "no CUDA libraries inside" in line and "CPU path only" in line and "checked statically" not in line


# ---- #764: release.yml builds the GPU pack in a parallel job and attaches it to the Release

def _gpu_pack(tmp_path, tag="v1.2.3", names=("ggml-cuda.dll", "cudart64_12.dll", "cublas64_12.dll",
                                              "cublasLt64_12.dll", "cudnn64_9.dll"), url=None):
    import hashlib
    import json

    z = _zip(tmp_path / release_check.GPU_PACK_ASSET, names)
    data = z.read_bytes()
    man = {"name": "gpu-pack", "size": len(data), "sha256": hashlib.sha256(data).hexdigest(), "wheels_key": "k",
           "url": url or f"https://github.com/o/r/releases/download/{tag}/{release_check.GPU_PACK_ASSET}"}
    z.with_name(z.name + release_check.GPU_PACK_MANIFEST_SUFFIX).write_text(json.dumps(man), encoding="utf-8")
    return z


def test_gpu_pack_asset_name_and_needs_match_build_windows():
    import importlib.util

    from tinglongwu import gpu_pack

    spec = importlib.util.spec_from_file_location("build_windows", REPO / "packaging" / "build_windows.py")
    bw = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bw)
    assert release_check.GPU_PACK_ASSET == bw.GPU_PACK_ASSET
    assert set(release_check.GPU_PACK_NEEDS) == {bw.GPU_PACK_LLAMA_DLL, *bw.LLAMA_CUDA_NEEDS}
    assert release_check.GPU_PACK_MANIFEST_KEYS == gpu_pack.MANIFEST_KEYS
    assert release_check.RELEASE_BUDGET_MIN == 20


def test_gpu_pack_check_passes_and_reports_size(tmp_path, monkeypatch):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    z = _gpu_pack(tmp_path)
    assert release_check.gpu_pack_problems(z, "v1.2.3") == []
    assert release_check.main(["--gpu-pack", str(z), "--tag", "v1.2.3"]) == 0
    assert "GPU pack size: 0.0 MB (tinglongwu-gpu-pack.zip, v1.2.3)" in summary.read_text("utf-8")


def test_gpu_pack_check_rejects_other_version_bad_hash_and_missing_dll(tmp_path):
    z = _gpu_pack(tmp_path, tag="v1.2.2")
    probs = release_check.gpu_pack_problems(z, "v1.2.3")
    assert len(probs) == 1 and "version mismatch" in probs[0]
    assert release_check.main(["--gpu-pack", str(z), "--tag", "v1.2.3"]) == 1
    (tmp_path / "b").mkdir()
    z = _gpu_pack(tmp_path / "b", names=("cudart64_12.dll", "cublas64_12.dll", "cublasLt64_12.dll"))
    assert release_check.gpu_pack_problems(z, "v1.2.3") == ["GPU pack lacks ggml-cuda.dll"]
    z.write_bytes(z.read_bytes() + b"x")  # changed after the manifest was written
    assert any("size" in p for p in release_check.gpu_pack_problems(z))
    assert any("sha256" in p for p in release_check.gpu_pack_problems(z))
    z.with_name(z.name + release_check.GPU_PACK_MANIFEST_SUFFIX).unlink()
    assert "manifest unreadable" in release_check.gpu_pack_problems(z)[0]
    assert release_check.main(["--gpu-pack", str(tmp_path / "none.zip")]) == 2


def test_release_wall_time_over_budget_warns_but_exits_zero(capsys):
    over = (release_check.RELEASE_BUDGET_MIN + 1) * release_check.SECONDS_PER_MIN
    assert release_check.main(["--release-start", "0"], now=over) == 0
    assert capsys.readouterr().out.startswith("::warning::release wall time: 21.0 min (OVER the 20 min budget)")
    assert release_check.main(["--release-start", "0"], now=5 * release_check.SECONDS_PER_MIN) == 0
    assert capsys.readouterr().out.startswith("release wall time: 5.0 min (within")


def test_release_gpu_pack_job_runs_beside_windows():
    jobs = _workflow("release.yml")["jobs"]
    gpu = jobs["gpu-pack"]
    assert gpu["needs"] == "plan" and gpu["if"] == jobs["windows"]["if"]  # parallel with windows, same dry-run rule
    assert gpu["permissions"] == {"contents": "read"}
    assert sorted(jobs["release"]["needs"]) == ["gpu-pack", "plan", "windows"]
    runs = [str(s.get("run", "")) for s in gpu["steps"]]
    text = "\n".join(runs)
    build = next(i for i, r in enumerate(runs) if "build_windows.py" in r)
    assert "--gpu-pack-only --out" in runs[build] and "/releases/download/$env:TAG/" + release_check.GPU_PACK_ASSET \
        in runs[build]
    assert "nuitka" not in text.lower()  # no Nuitka: the pack is only the CUDA DLLs
    assert "requirements-gpu.txt" in text and "requirements-llama-cuda.txt --only-binary=:all:" in text
    check = next(i for i, r in enumerate(runs) if "release_check.py --gpu-pack" in r)
    upload = next(i for i, s in enumerate(gpu["steps"]) if "upload-artifact" in str(s.get("uses", "")))
    assert build < check < upload and "--tag $env:TAG" in runs[check] and "exit $LASTEXITCODE" in runs[check]
    paths = gpu["steps"][upload]["with"]["path"]
    man = release_check.GPU_PACK_ASSET + release_check.GPU_PACK_MANIFEST_SUFFIX
    assert release_check.GPU_PACK_ASSET in paths and man in paths
    assert gpu["steps"][upload]["with"]["name"] == "release-gpu-pack"


def test_release_job_attaches_the_gpu_pack_and_lists_it_in_sha256sums():
    steps = _workflow("release.yml")["jobs"]["release"]["steps"]
    downloads = [s["with"]["name"] for s in steps if "download-artifact" in str(s.get("uses", ""))]
    assert downloads == ["release-win64", "release-gpu-pack"]
    runs = [str(s.get("run", "")) for s in steps]
    assert any("release_check.py --gpu-pack dist/" + release_check.GPU_PACK_ASSET in r and '--tag "$TAG"' in r
               for r in runs)
    sums = next(r for r in runs if "> SHA256SUMS.txt" in r)
    assert release_check.GPU_PACK_ASSET in sums and "sha256sum -c SHA256SUMS.txt" in sums
    create = next(r for r in runs if "gh release create" in r)
    files = create.split("files=(", 1)[1].split(")", 1)[0].split()
    man = release_check.GPU_PACK_ASSET + release_check.GPU_PACK_MANIFEST_SUFFIX
    assert {"dist/tinglongwu-win64.zip", f"dist/{release_check.GPU_PACK_ASSET}", f"dist/{man}",
            "dist/SHA256SUMS.txt"} <= set(files)
    wall = steps[-1]
    assert "release_check.py --release-start" in wall["run"] and wall["continue-on-error"] is True
    assert runs.index(create) < len(runs) - 1  # the wall time is taken after the release was created
