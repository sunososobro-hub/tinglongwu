"""#966: DLLs missing + CUDA device present -> the GPU-pack message, never "update your driver" (standalone build)."""
from tinglongwu import device


def _run(monkeypatch, bundled):
    if bundled:
        monkeypatch.setenv("TLWU_BUNDLED", "1")
    msgs = []
    out = device.resolve_device("auto", count=lambda: 1, missing=lambda: ["cublas64_12.dll", "cudnn64_9.dll"],
                                warn=msgs.append, driver=lambda: "560.94")
    return out, msgs


def test_gpu_pack_message_bundled(monkeypatch):
    out, msgs = _run(monkeypatch, True)
    assert out == "cpu" and len(msgs) == 1
    assert "GPU pack" in msgs[0] and "Add-ons" in msgs[0] and "cublas64_12.dll" in msgs[0]
    assert "update your NVIDIA" not in msgs[0]


def test_gpu_pack_message_source_install_keeps_pip_route(monkeypatch):
    out, msgs = _run(monkeypatch, False)
    assert out == "cpu" and "tlwu-setup --gpu" in msgs[0]


def test_gpu_pack_message_env_read_at_call_time(monkeypatch):
    monkeypatch.setenv("TLWU_BUNDLED", "1")  # set after device was imported, as tlwu.exe does
    assert "GPU pack" in device.gpu_pack_message(["cublas64_12.dll"])
