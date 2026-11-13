"""GPU pack (#613): manifest round-trip, bad hash rejected, pack dir absent -> CPU path without exception."""
import importlib.util
import zipfile
from pathlib import Path

import pytest

from tinglongwu import device, gpu_pack


def make_zip(p: Path, files=None):
    with zipfile.ZipFile(p, "w") as z:
        for n, b in (files or {"cublas64_12.dll": b"x" * 100}).items():
            z.writestr(n, b)
    return p


def test_manifest_round_trip(tmp_path):
    z = make_zip(tmp_path / "gpu.zip")
    man = gpu_pack.make_manifest(z, "https://example.invalid/gpu.zip")
    assert man["size"] == z.stat().st_size and len(man["sha256"]) == 64 and man["name"] == "gpu-pack"
    gpu_pack.write_manifest(man, tmp_path / "m.json")
    assert gpu_pack.load_manifest(tmp_path / "m.json") == man


def test_manifest_missing_key_rejected(tmp_path):
    (tmp_path / "m.json").write_text('{"name": "x"}')
    with pytest.raises(gpu_pack.PackError):
        gpu_pack.load_manifest(tmp_path / "m.json")


def test_install_ok_then_pack_dir_found(tmp_path):
    z = make_zip(tmp_path / "gpu.zip")
    dest = tmp_path / "app" / "gpu-pack"
    gpu_pack.install_pack(z, gpu_pack.make_manifest(z, "u"), dest)
    assert (dest / "cublas64_12.dll").read_bytes() == b"x" * 100
    assert gpu_pack.pack_dir(dest) == dest


def test_bad_hash_rejected_and_nothing_installed(tmp_path):
    z = make_zip(tmp_path / "gpu.zip")
    man = gpu_pack.make_manifest(z, "u")
    man["sha256"] = "0" * 64
    dest = tmp_path / "gpu-pack"
    with pytest.raises(gpu_pack.PackError, match="sha256"):
        gpu_pack.install_pack(z, man, dest)
    assert not dest.exists()


def test_bad_size_rejected(tmp_path):
    z = make_zip(tmp_path / "gpu.zip")
    man = gpu_pack.make_manifest(z, "u")
    man["size"] += 1
    with pytest.raises(gpu_pack.PackError, match="size"):
        gpu_pack.install_pack(z, man, tmp_path / "gpu-pack")


def test_zip_slip_rejected(tmp_path):
    z = make_zip(tmp_path / "gpu.zip", {"../evil.dll": b"x"})
    with pytest.raises(gpu_pack.PackError, match="unsafe"):
        gpu_pack.install_pack(z, gpu_pack.make_manifest(z, "u"), tmp_path / "gpu-pack")
    assert not (tmp_path / "evil.dll").exists()


def test_pack_dir_absent_means_cpu_without_exception(tmp_path, monkeypatch):
    monkeypatch.setenv("TLWU_GPU_PACK", str(tmp_path / "nope"))
    assert gpu_pack.pack_dir() is None
    assert device.resolve_device("auto", count=lambda: 0, missing=lambda: [], driver=lambda: None) == "cpu"
    assert device.resolve_device("auto", count=lambda: 1, missing=lambda: ["cublas64_12.dll"],
                                 warn=lambda m: None) == "cpu"
    device.register_cuda_libs()  # must not raise with no pack


def test_pack_dir_on_search_path_when_present(tmp_path, monkeypatch):
    (tmp_path / "gp").mkdir()
    (tmp_path / "gp" / "cudart64_12.dll").write_bytes(b"x")
    monkeypatch.setenv("TLWU_GPU_PACK", str(tmp_path / "gp"))
    assert tmp_path / "gp" in device._bundled_dirs()


def test_build_script_writes_manifest(tmp_path):
    spec = importlib.util.spec_from_file_location("build_windows", Path(__file__).parent.parent / "packaging/build_windows.py")
    bw = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bw)
    (tmp_path / "cuda_libs").mkdir()
    (tmp_path / "cuda_libs" / "cudart64_12.dll").write_bytes(b"abc")
    man_path = bw.make_gpu_pack(tmp_path, tmp_path / "gpu.zip", "https://example.invalid/gpu.zip")
    man = gpu_pack.load_manifest(man_path)
    gpu_pack.verify(tmp_path / "gpu.zip", man)


def test_reinstall_replaces_old_pack(tmp_path):
    dest = tmp_path / "gpu-pack"
    z1 = make_zip(tmp_path / "a.zip", {"old.dll": b"1"})
    gpu_pack.install_pack(z1, gpu_pack.make_manifest(z1, "u"), dest)
    z2 = make_zip(tmp_path / "b.zip", {"new.dll": b"2"})
    gpu_pack.install_pack(z2, gpu_pack.make_manifest(z2, "u"), dest)
    assert sorted(p.name for p in dest.iterdir()) == ["new.dll"]
    assert not (tmp_path / "gpu-pack.old").exists()


def test_failed_reinstall_keeps_old_pack(tmp_path):
    dest = tmp_path / "gpu-pack"
    z1 = make_zip(tmp_path / "a.zip", {"old.dll": b"1"})
    gpu_pack.install_pack(z1, gpu_pack.make_manifest(z1, "u"), dest)
    z2 = make_zip(tmp_path / "b.zip", {"new.dll": b"2"})
    man = gpu_pack.make_manifest(z2, "u")
    man["sha256"] = "f" * 64
    with pytest.raises(gpu_pack.PackError):
        gpu_pack.install_pack(z2, man, dest)
    assert (dest / "old.dll").read_bytes() == b"1"


def test_relative_env_pack_dir_is_absolute(tmp_path, monkeypatch):
    (tmp_path / "gp").mkdir()
    (tmp_path / "gp" / "x.dll").write_bytes(b"x")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TLWU_GPU_PACK", "gp")
    assert gpu_pack.pack_dir().is_absolute()


def test_build_script_rejects_empty_cuda_libs(tmp_path):
    spec = importlib.util.spec_from_file_location("build_windows", Path(__file__).parent.parent / "packaging/build_windows.py")
    bw = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bw)
    (tmp_path / "cuda_libs").mkdir()
    with pytest.raises(SystemExit):
        bw.make_gpu_pack(tmp_path, tmp_path / "gpu.zip", "u")
    assert not (tmp_path / "gpu.zip").exists()
