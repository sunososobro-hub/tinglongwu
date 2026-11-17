"""#783 (part 2 of #747): the whisper.cpp Vulkan ASR build ships in the Vulkan pack under whisper/, and
tinglongwu.asr_vulkan loads it through a flat C API (ctypes); any missing piece falls back to the CPU engine."""
import hashlib
import importlib.util
import json
import zipfile
from pathlib import Path

import numpy as np
import pytest

from tinglongwu import __version__ as APP_VERSION, asr_vulkan as av
from tinglongwu_setup import vulkan_pack

REPO = Path(__file__).resolve().parent.parent


def _script(name):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class _Fn:
    def __init__(self, impl):
        self.impl, self.restype, self.argtypes = impl, None, None

    def __call__(self, *a):
        return self.impl(*a)


class FakeLib:
    """Stands in for the tlwu-whisper library: same four C functions, records what Python passed."""

    def __init__(self, abi=av.WHISPER_ABI, text=b" hello ", lang=b"ja", rc=0, ctx=1234):
        self.calls, self.freed = [], []

        def transcribe(ctx_, pcm, n, lang_, threads, text_buf, text_len, lang_buf, lang_len, ns, lp):
            samples = [pcm[i] for i in range(n)]
            self.calls.append((ctx_, samples, lang_, threads, text_len, lang_len))
            text_buf.value, lang_buf.value = text, lang
            ns._obj.value, lp._obj.value = 0.25, -0.5
            return rc

        self.tlwu_whisper_abi = _Fn(lambda: abi)
        self.tlwu_whisper_init = _Fn(lambda path, gpu: (self.calls.append(("init", path, gpu)), ctx)[1])
        self.tlwu_whisper_free = _Fn(lambda c: self.freed.append(c))
        self.tlwu_whisper_transcribe = _Fn(transcribe)


def _files(tmp_path, lib=True, model=True):
    lib_path, model_path = tmp_path / av.WHISPER_LIB, tmp_path / "ggml-small.bin"
    if lib:
        lib_path.write_bytes(b"lib")
    if model:
        model_path.write_bytes(b"model")
    return lib_path, model_path


def test_backend_passes_audio_and_language_and_returns_the_recognizer_tuple(tmp_path):
    fake = FakeLib()
    b = av.WhisperCppBackend(*_files(tmp_path), load=lambda p: fake, threads=3)
    r = b.transcribe(np.array([0.5, -0.25], dtype=np.float64), "ja")  # float64 in -> float32 to C
    assert r == ("hello", "ja", 0.25, -0.5)
    init, (ctx, samples, lang, threads, text_len, lang_len) = fake.calls
    assert init == ("init", str(tmp_path / "ggml-small.bin").encode(), 1)
    assert (ctx, samples, lang, threads) == (1234, [0.5, -0.25], b"ja", 3)
    assert b.transcribe(np.zeros(4, np.float32), None) and fake.calls[-1][2] == b"auto"
    b.close()
    b.close()
    assert fake.freed == [1234]  # freed once


def test_empty_text_is_no_speech_and_a_failed_call_raises(tmp_path):
    assert av.WhisperCppBackend(*_files(tmp_path), load=lambda p: FakeLib(text=b"  ")).transcribe(
        np.zeros(4, np.float32), "en") is None
    with pytest.raises(RuntimeError, match="rc=-1"):
        av.WhisperCppBackend(*_files(tmp_path), load=lambda p: FakeLib(rc=-1)).transcribe(np.zeros(4, np.float32), "en")


@pytest.mark.parametrize("case, exc", [("abi", ImportError), ("nolib", ImportError), ("nomodel", FileNotFoundError),
                                       ("noctx", RuntimeError)])
def test_load_failures_raise_before_any_transcribe(tmp_path, case, exc):
    lib, model = _files(tmp_path, lib=case != "nolib", model=case != "nomodel")
    fake = FakeLib(abi=99 if case == "abi" else av.WHISPER_ABI, ctx=0 if case == "noctx" else 1)
    with pytest.raises(exc):
        av.WhisperCppBackend(lib, model, load=lambda p: fake)


def test_load_backend_finds_pack_whisper_dir_and_models_whisper_cpp(tmp_path):
    pack, models = tmp_path / "vulkan-pack", tmp_path / "models"
    (pack / av.WHISPER_DIR).mkdir(parents=True)
    (pack / av.WHISPER_DIR / av.WHISPER_LIB).write_bytes(b"lib")
    (models / av.MODEL_DIR).mkdir(parents=True)
    (models / av.MODEL_DIR / "ggml-base.bin").write_bytes(b"m")
    loaded = []
    b = av._load_backend(pack, models, "base", load=lambda p: (loaded.append(p), FakeLib())[1])
    assert loaded == [str(pack / "whisper" / av.WHISPER_LIB)] and isinstance(b, av.WhisperCppBackend)
    with pytest.raises(ImportError):
        av._load_backend(None, models, "base")


def test_llama_only_pack_falls_back_to_cpu_with_one_warning(tmp_path):
    """A pack from before #783 (no whisper/) must not break ASR: the real loader raises, select_recognizer -> CPU."""
    msgs = []
    rec, used = av.select_recognizer(tmp_path, cuda=False, vulkan=True, env={}, warn=msgs.append,
                                     cpu_factory=lambda d: "cpu-engine",
                                     vulkan_factory=lambda: av.WhisperCppVulkanRecognizer(tmp_path, pack_dir=tmp_path))
    assert (rec, used) == ("cpu-engine", "cpu") and len(msgs) == 1 and "ImportError" in msgs[0]


def _whisper_out(root: Path, lib: str) -> Path:
    root.mkdir()
    (root / lib).write_bytes(b"whisper-" + lib.encode())
    (root / "LICENSE-whisper.cpp.txt").write_text("MIT")
    (root / "whisper-cpp-commit.txt").write_text(_script("build_whisper_vulkan").WHISPER_COMMIT + "\n")
    return root


@pytest.mark.parametrize("plat, names, lib", [
    ("windows", ("llama.dll", "ggml.dll", "ggml-vulkan.dll"), "tlwu-whisper.dll"),
    ("linux", ("libllama.so", "libggml.so", "libggml-vulkan.so"), "libtlwu-whisper.so")])
def test_add_whisper_puts_the_build_under_whisper_and_keeps_the_llama_pack(tmp_path, plat, names, lib):
    mvp = _script("make_vulkan_pack")
    src = tmp_path / "lib"
    src.mkdir()
    for n in names:
        (src / n).write_bytes(b"dll")
    z = mvp.build(src, tmp_path / "out", APP_VERSION)
    added = mvp.add_whisper(z, _whisper_out(tmp_path / "w", lib))
    assert added == ["whisper/LICENSE-whisper.cpp.txt", f"whisper/{lib}", "whisper/whisper-cpp-commit.txt"]
    assert mvp.add_whisper(z, tmp_path / "w") == added  # idempotent: no duplicate members
    with zipfile.ZipFile(z) as zf:
        assert sorted(zf.namelist()) == sorted([*names, mvp.MANIFEST_NAME, mvp.VERSION_FILE, *added])
        m = json.loads(zf.read(mvp.MANIFEST_NAME))
    assert m["whisper_cpp"] == _script("build_whisper_vulkan").WHISPER_COMMIT and f"whisper/{lib}" in m["files"]
    assert mvp.accepted(z)  # still a pack mt_backend.vulkan_pack_dir() takes
    other = "libtlwu-whisper.so" if plat == "windows" else "tlwu-whisper.dll"
    with pytest.raises(SystemExit):  # the other platform's build never goes into this zip
        mvp.add_whisper(z, _whisper_out(tmp_path / "x", other))


def test_installed_pack_keeps_whisper_dir_and_a_tampered_zip_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(vulkan_pack, "HOST", "win32")
    mvp = _script("make_vulkan_pack")
    src = tmp_path / "lib"
    src.mkdir()
    for n in ("llama.dll", "ggml-vulkan.dll"):
        (src / n).write_bytes(b"dll")
    z = mvp.build(src, tmp_path / "out", APP_VERSION)
    mvp.add_whisper(z, _whisper_out(tmp_path / "w", "tlwu-whisper.dll"))
    sha = hashlib.sha256(z.read_bytes()).hexdigest()
    dest = vulkan_pack.install_zip(z, sha, tmp_path / "app" / "vulkan-pack", APP_VERSION)
    assert (dest / "whisper" / "tlwu-whisper.dll").read_bytes() == b"whisper-tlwu-whisper.dll"
    z.write_bytes(z.read_bytes() + b"x")  # tampered body
    with pytest.raises(vulkan_pack.PackError, match="sha256"):
        vulkan_pack.install_zip(z, sha, tmp_path / "app2" / "vulkan-pack", APP_VERSION)


def test_build_script_fetches_exactly_the_pinned_commit(tmp_path):
    bwv = _script("build_whisper_vulkan")
    cmds = []

    class Done:
        stdout = bwv.WHISPER_COMMIT + "\n"

    bwv.checkout(tmp_path, run=lambda cmd, **kw: (cmds.append(cmd), Done())[1])
    assert ["git", "fetch", "-q", "--depth", "1", bwv.REPO, bwv.WHISPER_COMMIT] in cmds
    Done.stdout = "0" * 40
    with pytest.raises(SystemExit, match="pin"):
        bwv.checkout(tmp_path, run=lambda cmd, **kw: Done())


def test_whisper_job_has_the_vulkan_pack_budget_and_sdk_pin():
    yaml = pytest.importorskip("yaml")
    jobs = yaml.safe_load((REPO / ".github" / "workflows" / "release.yml").read_text("utf-8"))["jobs"]
    w, vk = jobs["whisper-vulkan"], jobs["vulkan-pack"]
    budget = _script("release_check").CORE_JOB_BUDGET_MIN
    assert (w["timeout-minutes"], w["continue-on-error"]) == (budget, True)  # never delays or gates a release
    for k in ("VULKAN_SDK_VERSION", "VULKAN_SDK_SHA256"):
        assert w["env"][k] == vk["env"][k]
    assert "whisper-vulkan" in jobs["release"]["needs"]



def test_closed_backend_never_passes_a_freed_context_to_c(tmp_path):
    fake = FakeLib()
    b = av.WhisperCppBackend(*_files(tmp_path), load=lambda p: fake)
    b.close()
    n = len(fake.calls)
    with pytest.raises(RuntimeError, match="closed"):
        b.transcribe(np.zeros(4, np.float32), "en")
    assert len(fake.calls) == n and fake.freed == [1234]
    failed = av.WhisperCppBackend.__new__(av.WhisperCppBackend)
    (tmp_path / "lib-ok").write_bytes(b"lib")
    with pytest.raises(FileNotFoundError):
        failed.__init__(tmp_path / "lib-ok", tmp_path / "none.bin", load=lambda p: fake)
    failed.close()  # a backend whose __init__ raised closes quietly (as __del__ does)
