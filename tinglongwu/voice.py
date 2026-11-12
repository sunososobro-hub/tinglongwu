"""#330: optional spoken translation for Listen (`tlwu --voice`), e.g. Russian speech -> Chinese voice in earphones.

Off by default. The translated line is still written to the subtitle sink first and unchanged; the voice is
synthesized on its own thread (Kokoro in its own venv via TTSClient, CPU) and played on an output device that the
listen side does not record (`voice_output_problem` rejects anything that would loop back into the capture).
"""
from __future__ import annotations

import os
import queue
import threading
from pathlib import Path

from .sink import Result, Sink

VOICE_LANGS = ("zh", "en", "ja")  # targets with a Kokoro voice (KOKORO_*_VOICES in reverse/tts_worker.py)
# Outputs that feed a capture path or are not a real speaker: TTS played there is recorded again (feedback loop).
_LOOP_DEVICES = ("cable input", "cable output", "vb-audio", "blackhole", "tlwu_sink", "tlwu_mic", "multi-output",
                 "aggregate", "monitor of", "loopback")


def voice_lang(to: str) -> str | None:
    """Kokoro language for a --to code (zh-TW -> zh), or None when no built-in voice speaks it."""
    base = (to or "").split("-")[0].casefold()
    return base if base in VOICE_LANGS else None


def _norm(name: str | None) -> str:
    n = " ".join((name or "").casefold().split())
    return n.removeprefix("monitor of ").strip()


def voice_output_problem(out_name: str | None, capture_name: str | None) -> str | None:
    """Why playing the voice on `out_name` would be re-captured by the listen side, or None if it is safe.

    capture_name: the device the listen side records (the loopback speaker / its 'Monitor of' source, or the
    microphone); None for a file. Rejects the capture device itself, its loopback/monitor, and virtual cables."""
    out = " ".join((out_name or "").casefold().split())
    if not out:
        return "no output device"
    if any(k in out for k in _LOOP_DEVICES):
        return f"{out_name!r} is a virtual cable / loopback device that the listen side can record"
    cap = _norm(capture_name)
    if cap and (_norm(out) == cap or cap in out or out in cap):
        return f"{out_name!r} is the device being captured ({capture_name!r}): the voice would be translated again"
    return None


MAX_BACKLOG = 2  # unspoken lines kept while one is being spoken; older ones are skipped (the subtitle has them)


class VoiceSink(Sink):
    """Writes every line to `inner` (subtitles unchanged), then queues `r.target` for `speak(text)` on a worker
    thread, so a slow voice never delays the next subtitle. Lines are spoken in order; when more than
    `max_backlog` are waiting, the oldest waiting ones are skipped (counted in `dropped`), so the voice stays close
    to live speech instead of falling further behind."""

    def __init__(self, inner: Sink, speak, on_error=None, max_backlog: int = MAX_BACKLOG):
        self.inner, self.speak, self.max_backlog, self.dropped = inner, speak, max(1, max_backlog), 0
        self.on_error = on_error or (lambda e: None)
        self.q: queue.Queue = queue.Queue()
        self.t = threading.Thread(target=self._run, name="tlwu-voice", daemon=True)
        self.t.start()

    @property
    def shows_source(self) -> bool:  # #323 t_source_shown: only if the subtitle sink really shows source lines
        from .pipeline import sink_shows_source

        return sink_shows_source(self.inner)

    def write_source(self, src: str, original: str) -> None:
        self.inner.write_source(src, original)

    def write(self, r: Result) -> None:
        self.inner.write(r)
        text = (r.target or "").strip()
        if text:
            while self.q.qsize() >= self.max_backlog:
                try:
                    self.q.get_nowait()
                    self.dropped += 1
                except queue.Empty:
                    break
            self.q.put(text)

    def _run(self) -> None:
        while (text := self.q.get()) is not None:
            try:
                self.speak(text)
            except Exception as e:  # noqa: BLE001 - one bad line must not stop the voice or the subtitles
                self.on_error(e)

    def close(self, timeout: float = 30.0) -> bool:
        """Speak what is queued (at most max_backlog lines), then stop the worker. False if it is still speaking
        after `timeout` (the caller must then keep the TTS client and player open)."""
        self.q.put(None)
        self.t.join(timeout)
        return not self.t.is_alive()


def kokoro_paths(mdir: Path, tts_python: str | None = None, tts_model: str | None = None) -> tuple[str, str]:
    """(python of the Kokoro venv, Kokoro model dir): explicit values, else the tlwu-setup talk-mode defaults."""
    if not tts_python:
        venv = Path.home() / "tts-kokoro"
        tts_python = str(venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python"))
    return tts_python, tts_model or str(Path(mdir) / "kokoro")


def wait_drained(player, timeout: float = 60.0, poll: float = 0.02) -> None:
    """Block until `player` has handed all queued audio to the device (Player.push does not wait), so the next line
    waits in VoiceSink's bounded backlog instead of piling up as audio in Player.q."""
    import time

    q, end = getattr(player, "q", None), time.monotonic() + timeout
    while q is not None and not q.empty() and time.monotonic() < end:
        time.sleep(poll)


def make_speaker(tts, player):
    """speak(text) for VoiceSink: streamed Kokoro pieces (reverse/proto._synth_sentences) pushed to `player`;
    returns when that audio has been handed to the device."""
    from .reverse.proto import _synth_sentences

    def speak(text: str) -> None:
        info: dict = {}
        for audio, sr in _synth_sentences(tts, text, info=info):
            player.push(audio, sr)
        wait_drained(player)
        if info.get("errors"):  # e.g. misaki[zh] missing in the Kokoro venv: say why instead of staying silent
            raise RuntimeError(info["errors"][0])

    return speak


def capture_device_name(file: str | None, mic: bool, device: str | None) -> str | None:
    """Name of the device the listen side records (None for a file). Loopback capture records the default speaker
    unless --capture-device names another one."""
    if file:
        return None
    if device:
        return device
    try:
        import soundcard as sc

        return (sc.default_microphone() if mic else sc.default_speaker()).name
    except Exception:  # noqa: BLE001 - no audio stack: the explicit-name checks still apply
        return None


def open_voice(a, mdir, inner: Sink, say) -> tuple[VoiceSink, callable]:
    """Build the voice for parsed args `a` (`--voice`): Kokoro worker + player on a safe output device.
    Returns (sink wrapping `inner`, close()). Raises RuntimeError with a one-line reason."""
    from .reverse.proto import Player
    from .reverse.tts_client import TTSClient

    lang = voice_lang(a.to)
    if lang is None:
        raise RuntimeError(f"--voice speaks {', '.join(VOICE_LANGS)} only (Kokoro voices); --to is {a.to!r}")
    player = Player(a.voice_device, 48000)
    problem = voice_output_problem(player.speaker_name, capture_device_name(a.file, a.mic, a.device))
    if problem:
        player.close()
        raise RuntimeError(f"--voice: {problem}. Pick earphones with --voice-device NAME")
    py, model = kokoro_paths(mdir, a.tts_python, a.tts_model)
    try:
        tts = TTSClient(py, model, "kokoro", a.voice_speaker, lang)
    except (OSError, RuntimeError) as e:
        player.close()
        raise RuntimeError(f"--voice: Kokoro voice not available ({e}); run tlwu-setup --talk") from e
    say(f"tlwu: voice ({lang}, Kokoro) on {player.speaker_name}")
    sink = VoiceSink(inner, make_speaker(tts, player), on_error=lambda e: say(f"tlwu: voice error: {e}"))

    def close() -> None:
        if not sink.close():
            say("tlwu: voice still speaking at exit; left it running")
            return
        player.close()
        tts.close()

    return sink, close
