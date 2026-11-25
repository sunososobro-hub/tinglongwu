"""#1511: x86_64 emulator debug build: the AAR packer keeps every built ABI, and the CI ABI check
fails a release APK/AAB that carries anything but arm64-v8a."""
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHECK = ROOT / "scripts" / "check_apk_abis.py"
PACK = ROOT / "mobile" / "tool" / "pack_llama_aar.py"
LIBS = ("libllama", "libggml", "libggml-base", "libggml-cpu", "libmtmd")


def _run(*args):
    return subprocess.run([sys.executable, "-I", *map(str, args)], capture_output=True, text=True)


def _zip(path, names):
    with zipfile.ZipFile(path, "w") as z:
        for n in names:
            z.writestr(n, b"\x7fELF")
    return path


def _stage(tmp, abis):
    st = tmp / "stage"
    for abi in abis:
        (st / "jni" / abi).mkdir(parents=True)
        for n in LIBS:
            (st / "jni" / abi / (n + ".so")).write_bytes(b"\x7fELF" + abi.encode())
    (st / "AndroidManifest.xml").write_text("<manifest/>")
    (st / "R.txt").write_text("")
    return st


def test_pack_default_arm64_only(tmp_path):
    out = tmp_path / "a.aar"
    r = _run(PACK, _stage(tmp_path, ["arm64-v8a"]), out)
    assert r.returncode == 0, r.stderr
    names = zipfile.ZipFile(out).namelist()
    assert sorted(n for n in names if n.startswith("jni/")) == sorted("jni/arm64-v8a/%s.so" % n for n in LIBS)
    assert {"classes.jar", "AndroidManifest.xml", "R.txt"} <= set(names)


def test_pack_keeps_x86_64_slice(tmp_path):
    out = tmp_path / "a.aar"
    r = _run(PACK, _stage(tmp_path, ["arm64-v8a", "x86_64"]), out)
    assert r.returncode == 0, r.stderr
    assert "arm64-v8a x86_64" in r.stdout
    names = set(zipfile.ZipFile(out).namelist())
    for n in LIBS:
        assert "jni/x86_64/%s.so" % n in names and "jni/arm64-v8a/%s.so" % n in names


def test_pack_rejects_unknown_or_empty_abi(tmp_path):
    st = _stage(tmp_path, ["arm64-v8a"])
    (st / "jni" / "x86").mkdir()
    assert _run(PACK, st, tmp_path / "a.aar").returncode == 1
    (st / "jni" / "x86").rmdir()
    (st / "jni" / "x86_64").mkdir()
    assert _run(PACK, st, tmp_path / "a.aar").returncode == 1


def test_release_apk_arm64_only_passes(tmp_path):
    apk = _zip(tmp_path / "r.apk", ["lib/arm64-v8a/libflutter.so", "lib/arm64-v8a/libllama.so", "classes.dex",
                                    "assets/lib/x86_64/notes.txt"])
    r = _run(CHECK, apk, "arm64-v8a")
    assert r.returncode == 0, r.stderr
    assert "arm64-v8a (2 .so)" in r.stdout


def test_release_apk_with_x86_64_fails(tmp_path):
    apk = _zip(tmp_path / "r.apk", ["lib/arm64-v8a/libllama.so", "lib/x86_64/libllama.so"])
    r = _run(CHECK, apk, "arm64-v8a")
    assert r.returncode == 1
    assert "expected arm64-v8a" in r.stderr


def test_release_aab_checks_module_lib(tmp_path):
    ok = _zip(tmp_path / "ok.aab", ["base/lib/arm64-v8a/libllama.so", "base/dex/classes.dex"])
    assert _run(CHECK, ok, "arm64-v8a").returncode == 0
    bad = _zip(tmp_path / "bad.aab", ["base/lib/arm64-v8a/libllama.so", "base/lib/armeabi-v7a/libx.so"])
    assert _run(CHECK, bad, "arm64-v8a").returncode == 1


def test_debug_apk_needs_both_abis(tmp_path):
    both = _zip(tmp_path / "d.apk", ["lib/arm64-v8a/libllama.so", "lib/x86_64/libllama.so"])
    assert _run(CHECK, both, "arm64-v8a,x86_64").returncode == 0
    arm_only = _zip(tmp_path / "a.apk", ["lib/arm64-v8a/libllama.so"])
    r = _run(CHECK, arm_only, "arm64-v8a,x86_64")
    assert r.returncode == 1 and "ABIs arm64-v8a, expected arm64-v8a,x86_64" in r.stderr


def test_build_script_rejects_unknown_abi(tmp_path):
    # runs the real script up to the ABI loop: fake NDK dir, a local git repo standing in for llama.cpp
    sdk = tmp_path / "sdk"
    (sdk / "ndk" / "28.2.13676358").mkdir(parents=True)
    root = tmp_path / "root"
    src = root / "build" / "llama.cpp"
    src.mkdir(parents=True)
    git = ["git", "-C", str(src)]
    subprocess.run(git + ["init", "-q"], check=True)
    subprocess.run(git + ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "x"], check=True)
    sha = subprocess.run(git + ["rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    subprocess.run(git + ["remote", "add", "origin", str(src)], check=True)
    script = (ROOT / "mobile" / "tool" / "build_llama_android.sh").read_text()
    script = script.replace("COMMIT=afeebe103bd99cda8f5dfaefcabadf890db7fda7", "COMMIT=" + sha)
    copy = tmp_path / "mobile" / "tool" / "b.sh"
    copy.parent.mkdir(parents=True)
    copy.write_text(script)
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "ANDROID_HOME": str(sdk), "TLWU_BUILD_ROOT": str(root),
           "TLWU_LLAMA_ABIS": "x86"}
    r = subprocess.run(["bash", str(copy)], capture_output=True, text=True, env=env)
    assert r.returncode == 1
    assert "unsupported ABI in TLWU_LLAMA_ABIS: x86" in r.stderr
