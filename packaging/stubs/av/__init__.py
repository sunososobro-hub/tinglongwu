"""Stand-in for PyAV in the standalone Windows build.

The PyAV wheel bundles FFmpeg with GPL codecs (libx264/libx265), which must not ship with tinglongwu.
faster-whisper imports `av` at module level (only used to decode audio *files*); live capture, wav/flac/ogg/mp3
files (soundfile) and the GUI never need it. Anything that actually calls into PyAV gets a clear error.
"""


class AvNotBundled(RuntimeError):
    pass


def _fail(*_a, **_k):
    raise AvNotBundled("this build has no FFmpeg (PyAV): use wav/flac/ogg/mp3 files, live capture or --mic")


open = AudioResampler = _fail


class _NS:
    def __getattr__(self, name):
        return _NS() if not name.startswith("__") else _fail


audio = error = _NS()
