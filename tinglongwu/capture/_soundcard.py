"""Shared implementation for live capture through the `soundcard` library (BSD-3).

soundcard talks to WASAPI (Windows), PulseAudio/PipeWire-pulse (Linux) and
CoreAudio (macOS). It is imported lazily so the package imports everywhere.
"""
from __future__ import annotations

import contextlib
import struct
import sys
import threading
import warnings
from typing import Iterator

import numpy as np

from .base import AudioSource, to_mono_16k

NATIVE_RATE = 48000
BLOCK = NATIVE_RATE // 10  # 100 ms


def suppress_capture_warnings() -> None:
    """Hide soundcard's "data discontinuity in recording" noise. soundcard's mediafoundation module runs
    warnings.simplefilter('always', SoundcardRuntimeWarning) at import, which lands in FRONT of any filter
    registered earlier, so this must be called again after `import soundcard` (filters are process-global,
    so it also covers the capture thread)."""
    warnings.filterwarnings("ignore", message=".*data discontinuity.*")
    warnings.filterwarnings("ignore", category=RuntimeWarning, module=r"soundcard(\..*)?$")


def _load_ole32():
    import ctypes
    return ctypes.OleDLL("ole32") if hasattr(ctypes, "OleDLL") else ctypes.windll.ole32


@contextlib.contextmanager
def com_initialized():
    """Windows: initialize COM (MTA) on the calling thread. soundcard's mediafoundation only calls
    CoInitializeEx on the thread that imported it, so a capture worker thread would otherwise fail with
    0x800401F0 (CO_E_NOTINITIALIZED). S_FALSE (already initialized) still needs a matching
    CoUninitialize; RPC_E_CHANGED_MODE (thread already STA) is not an error and needs none. No-op elsewhere."""
    if sys.platform != "win32":
        yield
        return
    ole32 = _load_ole32()
    hr = ole32.CoInitializeEx(None, 0)  # COINIT_MULTITHREADED
    ok = (hr & 0xFFFFFFFF) in (0, 1)  # S_OK / S_FALSE
    try:
        yield
    finally:
        if ok:
            ole32.CoUninitialize()


WAVE_FORMAT_EXTENSIBLE = 0xFFFE
_SUBTYPE_IEEE_FLOAT = bytes.fromhex("0300000000001000800000aa00389b71")  # KSDATAFORMAT_SUBTYPE_IEEE_FLOAT


def float32_extensible(samplerate: int, channels: int) -> bytes:
    """A packed 40-byte WAVEFORMATEXTENSIBLE describing interleaved float32 (what soundcard reads and writes)."""
    mask = {1: 0x4, 2: 0x3}.get(channels, (1 << channels) - 1)  # front centre / front left+right
    return struct.pack("<HHIIHHHHI16s", WAVE_FORMAT_EXTENSIBLE, channels, int(samplerate),
                       int(samplerate) * channels * 4, channels * 4, 32, 22, 32, mask, _SUBTYPE_IEEE_FLOAT)


def _init_own_format(mf, client, ptr, samplerate, channels, blocksize, isloopback, exclusive_mode=False) -> None:
    """soundcard's _AudioClient.__init__ without the mix-format asserts: Initialize with our own float32 format.
    Shared mode with AUTOCONVERTPCM + SRC lets WASAPI convert from whatever the device mixes in."""
    client._ptr = ptr
    client.channelmap = list(range(channels)) if isinstance(channels, int) else list(channels)
    n = len(set(client.channelmap))
    if blocksize is None:
        blocksize = client.deviceperiod[0] * samplerate
    buf = mf._ffi.new("char[]", float32_extensible(samplerate, n))  # must stay alive until Initialize returns
    fmt = mf._ffi.cast("WAVEFORMATEXTENSIBLE*", buf)
    share = mf._ole32.AUDCLNT_SHAREMODE_EXCLUSIVE if exclusive_mode else mf._ole32.AUDCLNT_SHAREMODE_SHARED
    #       rate-adjust | autoconvert | better-SRC | nopersist   (same flags soundcard uses)
    flags = 0x00100000 | 0x80000000 | 0x08000000 | 0x00080000 | (0x00020000 if isloopback else 0)
    hr = ptr[0][0].lpVtbl.Initialize(ptr[0], share, flags, int(blocksize / samplerate * 10000000), 0, fmt,
                                     mf._ffi.NULL)
    mf._com.check_error(hr)
    client.samplerate = samplerate
    client._idle_start_time = None


def patch_com_teardown(mf=None) -> bool:
    """Windows (#312): soundcard's module-level `_com = _COMLibrary()` calls `_ole32.CoUninitialize()` in __del__.
    At interpreter exit the module's globals are often cleared first, so it prints
    "AttributeError: 'NoneType' object has no attribute 'CoUninitialize'" after every run. Wrap __del__ so that this
    shutdown-only failure is silent (the process is ending; COM goes away with it). Normal teardown is unchanged.
    Idempotent; a no-op when the WASAPI backend is not loaded."""
    mf = mf or sys.modules.get("soundcard.mediafoundation")
    cls = getattr(mf, "_COMLibrary", None)
    if cls is None:
        return False
    if getattr(cls, "_tlwu_teardown", False):
        return True
    orig = cls.__del__

    def _del(self):
        try:
            orig(self)
        except (AttributeError, TypeError):  # module globals (_ole32) already None during interpreter shutdown
            pass

    cls.__del__ = _del
    cls._tlwu_teardown = True
    return True


def patch_mixformat(mf=None) -> bool:
    """Windows (#130): soundcard 0.4.6 asserts that the device mix format is WAVE_FORMAT_EXTENSIBLE float and dies
    with an empty AssertionError on devices that report a plain WAVEFORMATEX (e.g. "High Definition Audio
    Device" microphones). Wrap _AudioClient.__init__ (recorders, loopback and players) so that those devices are
    opened with our own format instead. The asserts run before soundcard touches the client, so the same
    IAudioClient can still be initialized. Idempotent; a no-op when the WASAPI backend is not loaded."""
    mf = mf or sys.modules.get("soundcard.mediafoundation")
    patch_com_teardown(mf)  # #312: every soundcard user passes here once the backend is loaded
    cls = getattr(mf, "_AudioClient", None)
    if cls is None:
        return False
    if getattr(cls, "_tlwu_mixformat", False):
        return True
    orig = cls.__init__

    def init(self, ptr, samplerate, channels, blocksize, isloopback, exclusive_mode=False):
        try:
            orig(self, ptr, samplerate, channels, blocksize, isloopback, exclusive_mode)
        except AssertionError:
            _init_own_format(mf, self, ptr, samplerate, channels, blocksize, isloopback, exclusive_mode)

    cls.__init__ = init
    cls._tlwu_mixformat = True
    return True


# #317: WASAPI AUDCLNT_E_DEVICE_INVALIDATED / AUDCLNT_E_RESOURCES_INVALIDATED (soundcard raises
# RuntimeError("Error 0x88890004")); the recorder's __exit__ then fails with "Error 0x100000001".
DEVICE_LOST_CODES = ("0x88890004", "0x88890026")
REOPEN_LIMIT = 5        # consecutive failed reopen attempts before giving up
BACKOFF_FIRST = 0.5     # seconds, doubled per consecutive failure
BACKOFF_MAX = 5.0


class AudioDeviceLost(RuntimeError):
    """The capture device went away and could not be reopened (one-line message, no traceback)."""


def device_lost(e: BaseException) -> bool:
    return isinstance(e, RuntimeError) and any(c in str(e).lower() for c in DEVICE_LOST_CODES)


def backoff(failures: int) -> float:
    """Wait before reopen attempt number `failures` (1-based): 0.5, 1, 2, 4, 5, 5 ... seconds."""
    return min(BACKOFF_FIRST * 2 ** (failures - 1), BACKOFF_MAX)


def _short(e: BaseException) -> str:
    return (str(e) or type(e).__name__).splitlines()[0][:120]


@contextlib.contextmanager
def _recorder(mic):
    """mic.recorder() whose __exit__ error (Error 0x100000001 on an invalidated device) never hides the device-loss
    error that ended the block; on a normal exit its errors still surface."""
    cm = mic.recorder(samplerate=NATIVE_RATE, channels=2)
    rec = cm.__enter__()
    try:
        yield rec
    except BaseException as e:
        try:
            cm.__exit__(type(e), e, e.__traceback__)
        except Exception:  # noqa: BLE001 - the device is already gone; report the original error
            if not device_lost(e):
                raise
        raise
    else:
        cm.__exit__(None, None, None)


class SoundcardSource(AudioSource):
    def __init__(self, device: str | None = None):
        self.device = device
        self._stop = threading.Event()

    def _pick_microphone(self, sc):  # overridden per platform
        raise NotImplementedError

    def frames(self) -> Iterator[np.ndarray]:
        # Import BEFORE entering our COM scope: soundcard's import-time CoInitializeEx treats S_FALSE
        # ("already initialized") as a fatal "Error 0x100000001". If the import happens on this thread it
        # initializes COM itself (our call then gets S_FALSE, which is balanced); if it happened on another
        # thread earlier, our call is what initializes this one.
        import soundcard  # noqa: F401
        patch_mixformat()
        with com_initialized():
            yield from self._frames()

    def _frames(self) -> Iterator[np.ndarray]:
        """Yield 100 ms blocks. #317: when the OS invalidates the device (default device switched, headset plugged,
        Meet changing devices), close the recorder, re-pick the device and keep going; give up after
        REOPEN_LIMIT consecutive failures with one clear AudioDeviceLost line."""
        import soundcard as sc

        suppress_capture_warnings()
        failures, last = 0, None
        while not self._stop.is_set():
            try:
                mic = self._pick_microphone(sc) if failures == 0 else self._repick(sc)
                with _recorder(mic) as rec:
                    if failures:
                        print(f"tlwu: audio device changed, reopened {getattr(mic, 'name', mic)}",
                              file=sys.stderr, flush=True)
                    # one 100 ms block per iteration: close() is noticed within a block even in total silence,
                    # and leaving the `with` releases the device (OS microphone indicator goes off).
                    while not self._stop.is_set():
                        data = rec.record(numframes=BLOCK)
                        if self._stop.is_set():
                            break
                        failures = 0  # a good block: the next invalidation starts a fresh retry budget
                        yield to_mono_16k(data, NATIVE_RATE)
                return
            except Exception as e:  # noqa: BLE001 - only device loss is retried, everything else re-raised
                if self._stop.is_set():
                    return
                if not (failures or device_lost(e)):
                    raise
                failures, last = failures + 1, e
                if failures > REOPEN_LIMIT:
                    raise AudioDeviceLost(f"audio device lost and could not be reopened after {REOPEN_LIMIT} "
                                          f"tries (last error: {_short(last)})") from None
                delay = backoff(failures)
                print(f"tlwu: audio device lost ({_short(e)}), reopening in {delay:g} s "
                      f"({failures}/{REOPEN_LIMIT})", file=sys.stderr, flush=True)
                self._stop.wait(delay)

    def _repick(self, sc):
        """Re-pick after a device loss: the configured name first, the system default if that device is gone."""
        try:
            return self._pick_microphone(sc)
        except RuntimeError:
            if not self.device:
                raise
            saved, self.device = self.device, None
            try:
                return self._pick_microphone(sc)
            finally:
                self.device = saved

    def close(self) -> None:
        self._stop.set()


def find_by_name(sc, name: str, include_loopback: bool = True):
    name = name.lower()
    for m in sc.all_microphones(include_loopback=include_loopback):
        if name in m.name.lower():
            return m
    raise RuntimeError(f"No capture device matching {name!r}. Available: "
                       f"{[m.name for m in sc.all_microphones(include_loopback=include_loopback)]}")
