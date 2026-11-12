"""#130: a microphone whose WASAPI mix format is a plain WAVEFORMATEX (not WAVE_FORMAT_EXTENSIBLE) made soundcard
0.4.6 die with an empty AssertionError; speak mode printed just "input stopped:". Fakes stand in for the COM
objects; the real cffi builds the pointer so the bytes handed to IAudioClient::Initialize are what is checked."""
import struct
import sys
import types
from types import SimpleNamespace

import cffi

from tinglongwu.capture import _soundcard as sc

FLOAT_GUID = bytes.fromhex("0300000000001000800000aa00389b71")


class FakeVtbl:
    def __init__(self, ffi, hr=0):
        self.ffi, self.hr, self.calls = ffi, hr, []

    def Initialize(self, this, share, flags, duration, period, fmt, session):
        self.calls.append(SimpleNamespace(share=share, flags=flags, duration=duration,
                                          fmt=bytes(self.ffi.buffer(self.ffi.cast("char*", fmt), 40))))
        return self.hr


def fake_backend(extensible: bool, hr=0):
    """A stand-in for soundcard.mediafoundation with the same __init__ contract (asserts on the mix format)."""
    ffi = cffi.FFI()
    ffi.cdef("typedef struct { char raw[40]; } WAVEFORMATEXTENSIBLE;")
    vtbl = FakeVtbl(ffi, hr)
    checked = []

    class _AudioClient:
        upstream_ok = False

        def __init__(self, ptr, samplerate, channels, blocksize, isloopback, exclusive_mode=False):
            self._ptr = ptr
            self.channelmap = list(range(channels))
            assert (0xFFFE if extensible else 1) == 0xFFFE  # mediafoundation.py:516
            self.upstream_ok = True
            self.samplerate = samplerate

        @property
        def deviceperiod(self):
            return 0.01, 0.003

    def check_error(code):
        checked.append(code)
        if code:
            raise RuntimeError(f"Error {code:#x}")

    mf = SimpleNamespace(_ffi=ffi, _AudioClient=_AudioClient, _com=SimpleNamespace(check_error=check_error),
                         _ole32=SimpleNamespace(AUDCLNT_SHAREMODE_SHARED=0, AUDCLNT_SHAREMODE_EXCLUSIVE=1))
    ptr = [[SimpleNamespace(lpVtbl=vtbl)]]
    return mf, ptr, vtbl, checked


def test_format_is_packed_float32_extensible():
    raw = sc.float32_extensible(48000, 2)
    assert len(raw) == 40
    tag, ch, rate, avg, align, bits, cb, valid, mask, guid = struct.unpack("<HHIIHHHHI16s", raw)
    assert (tag, ch, rate, avg, align, bits, cb, valid, mask) == (0xFFFE, 2, 48000, 48000 * 8, 8, 32, 22, 32, 3)
    assert guid == FLOAT_GUID
    assert struct.unpack("<HHIIHHHHI16s", sc.float32_extensible(16000, 1))[8] == 0x4  # mono = front centre


def test_non_extensible_microphone_opens_with_our_format():
    mf, ptr, vtbl, checked = fake_backend(extensible=False)
    assert sc.patch_mixformat(mf) is True
    client = mf._AudioClient(ptr, 48000, 2, None, False)  # raised AssertionError before the fix
    assert len(vtbl.calls) == 1 and checked == [0]
    call = vtbl.calls[0]
    assert call.fmt == sc.float32_extensible(48000, 2)
    assert call.share == 0 and call.flags & 0x80000000 and not call.flags & 0x00020000
    assert call.duration == 100000  # default device period 10 ms, in 100 ns units
    assert client.samplerate == 48000 and client.channelmap == [0, 1] and client._idle_start_time is None


def test_loopback_flag_and_blocksize_are_kept():
    mf, ptr, vtbl, _ = fake_backend(extensible=False)
    sc.patch_mixformat(mf)
    mf._AudioClient(ptr, 48000, 1, 4800, True)
    call = vtbl.calls[0]
    assert call.flags & 0x00020000 and call.duration == 1000000 and call.fmt == sc.float32_extensible(48000, 1)


def test_extensible_device_still_uses_soundcard_path():
    mf, ptr, vtbl, _ = fake_backend(extensible=True)
    sc.patch_mixformat(mf)
    client = mf._AudioClient(ptr, 48000, 2, None, False)
    assert client.upstream_ok and vtbl.calls == []


def test_patch_is_idempotent_and_real_errors_still_raise():
    mf, ptr, vtbl, _ = fake_backend(extensible=False, hr=0x88890008)
    sc.patch_mixformat(mf)
    first = mf._AudioClient.__init__
    sc.patch_mixformat(mf)
    assert mf._AudioClient.__init__ is first
    try:
        mf._AudioClient(ptr, 48000, 2, None, False)
    except RuntimeError as e:
        assert "0x88890008" in str(e)
    else:
        raise AssertionError("an Initialize failure must not be swallowed")
    assert sc.patch_mixformat(SimpleNamespace()) is False  # backend without _AudioClient (Linux/macOS): no-op


def test_capture_source_applies_the_patch(monkeypatch):
    """SoundcardSource.frames() on Windows records from a non-extensible microphone instead of raising."""
    import numpy as np

    from tinglongwu.capture.base import to_mono_16k

    to_mono_16k(np.zeros((480, 2), dtype=np.float32), sc.NATIVE_RATE)  # load the resampler (scipy) before faking win32
    mf, ptr, vtbl, _ = fake_backend(extensible=False)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sc, "_load_ole32", lambda: SimpleNamespace(CoInitializeEx=lambda *a: 0,
                                                                   CoUninitialize=lambda: None))
    monkeypatch.setitem(sys.modules, "soundcard", types.ModuleType("soundcard"))
    monkeypatch.setitem(sys.modules, "soundcard.mediafoundation", mf)

    class Recorder:
        def __init__(self):
            self.client = mf._AudioClient(ptr, sc.NATIVE_RATE, 2, None, False)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def record(self, numframes):
            return np.zeros((numframes, 2), dtype=np.float32)

    class Src(sc.SoundcardSource):
        def _pick_microphone(self, s):
            return SimpleNamespace(recorder=lambda samplerate, channels: Recorder())

    src = Src()
    it = src.frames()
    block = next(it)
    src.close()
    assert len(block) == 1600 and len(vtbl.calls) == 1


def test_input_stopped_names_the_exception(capsys):
    """Speak mode: a message-less exception from the microphone is reported by type, with a traceback."""
    from tinglongwu.reverse import proto

    assert proto.describe_error(AssertionError()) == "AssertionError"
    assert proto.describe_error(RuntimeError("no mic")) == "RuntimeError: no mic"

    def items():
        raise AssertionError()
        yield

    tts = SimpleNamespace(engine="none", speak=lambda text: iter(()))
    player = SimpleNamespace(push=lambda *a: None)
    res = {"items": []}
    proto._run_confirm(SimpleNamespace(to="ru"), items(), None, tts, player, SimpleNamespace(enabled=False), res)
    out, err = capsys.readouterr()
    assert "input stopped: AssertionError" in out
    assert "Traceback" in err and "AssertionError" in err
