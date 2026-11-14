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

from .sink import Result, Sink, pass_source

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


def _virtual(name: str) -> bool:
    return any(k in name for k in _LOOP_DEVICES)


def voice_output_problem(out_name: str | None, capture_name: str | None, mic: bool = False) -> str | None:
    """Why playing the voice on `out_name` would be re-captured by the listen side, or None if it is safe.

    capture_name: the device the listen side records (the loopback speaker / its 'Monitor of' source, or the
    microphone); None for a file. Rejects the capture device itself, its loopback/monitor, and virtual cables.
    mic=True (#710, speak into a meeting): a virtual cable such as VB-CABLE "CABLE Input" is allowed when the
    capture is a known real microphone (not itself a cable), because then nothing records the cable's other end."""
    out = " ".join((out_name or "").casefold().split())
    if not out:
        return "no output device"
    cap = _norm(capture_name)
    if _virtual(out) and not (mic and cap and not _virtual(" ".join((capture_name or "").casefold().split()))):
        return f"{out_name!r} is a virtual cable / loopback device that the listen side can record"
    if cap and (_norm(out) == cap or cap in out or out in cap):
        return f"{out_name!r} is the device being captured ({capture_name!r}): the voice would be translated again"
    return None


class VoiceUnavailable(RuntimeError):
    """#885: the voice cannot start. str() is the CLI line (flags and device names, unchanged); `user` is one plain
    line in the UI language for the GUI, which then keeps listening without the voice."""

    def __init__(self, cli_msg: str, key: str):
        super().__init__(cli_msg)
        self.key = key

    @property
    def user(self) -> str:
        from .i18n import t

        return t(self.key)


def problem_why(out_name: str | None, capture_name: str | None, mic: bool = False) -> str | None:
    """voice_output_problem() as a short reason in the UI language (#885), or None if the output is safe."""
    if voice_output_problem(out_name, capture_name, mic) is None:
        return None
    from .i18n import t

    return t("voice_why_loop")


CLOSE_TIMEOUT = 30.0  # seconds VoiceSink.close() waits for the backlog (<= MAX_BACKLOG lines) to be spoken
MAX_BACKLOG = 2  # unspoken lines kept while one is being spoken; older ones are skipped (the subtitle has them)


class VoiceSink(Sink):
    """Writes every line to `inner` (subtitles unchanged), then queues `r.target` for `speak(text)` on a worker
    thread, so a slow voice never delays the next subtitle. Lines are spoken in order; when more than
    `max_backlog` are waiting, the oldest waiting ones are skipped (counted in `dropped`), so the voice stays close
    to live speech instead of falling further behind."""

    def __init__(self, inner: Sink, speak, on_error=None, max_backlog: int = MAX_BACKLOG):
        self.inner, self.speak, self.max_backlog, self.dropped = inner, speak, max(1, max_backlog), 0
        self.on_error = on_error or (lambda e: None)
        self.muted: str | None = None  # reason, once mute() has turned the voice off
        self.capture_guard = None  # #710: on_reopen(name) for the capture source, set by open_voice for a cable
        self.q: queue.Queue = queue.Queue()
        self.t = threading.Thread(target=self._run, name="tlwu-voice", daemon=True)
        self.t.start()

    @property
    def shows_source(self) -> bool:  # #323 t_source_shown: only if the subtitle sink really shows source lines
        from .pipeline import sink_shows_source

        return sink_shows_source(self.inner)

    def write_source(self, src: str, original: str, speaker: str | None = None) -> None:
        pass_source(self.inner, src, original, speaker)

    def mute(self, reason: str) -> bool:
        """Stop speaking for the rest of the session (subtitles continue); waiting lines are dropped. True the
        first time only."""
        if self.muted:
            return False
        self.muted = reason
        while True:
            try:
                self.q.get_nowait()
            except queue.Empty:
                break
        return True

    def write(self, r: Result) -> None:
        self.inner.write(r)
        text = (r.target or "").strip()
        if text and not self.muted:
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

    def pending(self) -> int:
        """Lines still waiting to be spoken (the stop sentinel is not a line)."""
        with self.q.mutex:
            return sum(1 for x in self.q.queue if x is not None)

    def close(self, timeout: float | None = None, drop: bool = False) -> bool:
        """Speak what is queued (at most max_backlog lines), then stop the worker. drop=True (Ctrl+C / Stop):
        discard the waiting lines instead, only the line being spoken finishes. False if it is still speaking
        after `timeout` (the caller must then keep the TTS client and player open)."""
        if drop:
            while True:
                try:
                    self.q.get_nowait()
                except queue.Empty:
                    break
        self.q.put(None)
        self.t.join(CLOSE_TIMEOUT if timeout is None else timeout)
        return not self.t.is_alive()


def cable_guard(sink: "VoiceSink", out_name: str, say, off=None):
    """#710 (review of #749): on_reopen(name) for the capture source while the voice plays into a virtual cable.
    The mic was checked at start, but a device change can reopen the recorder on another device (e.g. the system
    default falls back to "CABLE Output"); then the voice is muted for good instead of feeding itself back into
    the meeting. Subtitles continue."""

    def guard(capture_name: str | None) -> None:
        problem = voice_output_problem(out_name, capture_name, mic=True)
        if problem and sink.mute(problem):
            say(f"tlwu: voice off after the microphone changed: {problem}")
            if off is not None:
                from .i18n import t

                off(t("voice_off", why=problem_why(out_name, capture_name, mic=True)))

    return guard


def kokoro_paths(mdir: Path, tts_python: str | None = None, tts_model: str | None = None) -> tuple[str, str]:
    """(python of the Kokoro venv, Kokoro model dir): explicit values, else the tlwu-setup talk-mode defaults."""
    if not tts_python:
        venv = Path.home() / "tts-kokoro"
        tts_python = str(venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python"))
    return tts_python, tts_model or str(Path(mdir) / "kokoro")


def wait_drained(player, timeout: float = 60.0, poll: float = 0.02) -> bool:
    """Block until `player` has handed all queued audio to the device (Player.push does not wait), so the next line
    waits in VoiceSink's bounded backlog instead of piling up as audio in Player.q. At most the last piece is then
    still playing (the next line's synthesis overlaps it: no gap). False if playback stalled for `timeout`."""
    import time

    q, end = getattr(player, "q", None), time.monotonic() + timeout
    while q is not None and not q.empty():
        if time.monotonic() >= end:
            return False
        time.sleep(poll)
    return True


def make_speaker(tts, player, drain_timeout: float = 60.0, on_off=None):
    """speak(text) for VoiceSink: streamed Kokoro pieces (reverse/proto._synth_sentences) pushed to `player`;
    returns when that audio has been handed to the device. While playback is stalled no more audio is pushed: the
    line is skipped with an error (so Player.q stays bounded). on_off(str) is called once when the voice turns
    itself off (garbled worker reply), so the GUI can show a lasting "voice off" state (#448)."""
    from .reverse.proto import _synth_sentences

    broken: list[str] = []

    def speak(text: str) -> None:
        if broken:  # the worker's reply stream is out of sync: every later line would be garbage too
            return
        if not wait_drained(player, drain_timeout):
            raise RuntimeError("playback stalled; line not spoken")
        info: dict = {}
        try:
            for audio, sr in _synth_sentences(tts, text, info=info):
                player.push(audio, sr)
        except (ValueError, KeyError) as e:  # garbled header from the Kokoro worker (bad JSON / missing field)
            broken.append(str(e))
            if on_off is not None:
                from .i18n import t

                on_off(t("voice_off", why=t("voice_why_garbled")))
            raise RuntimeError(f"Kokoro worker sent a garbled reply ({e}); voice off, subtitles continue") from e
        if not wait_drained(player, drain_timeout):
            raise RuntimeError("playback stalled; this line may not have been heard")
        if info.get("errors"):  # e.g. misaki[zh] missing in the Kokoro venv: say why instead of staying silent
            raise RuntimeError(info["errors"][0])

    return speak


def resolve_capture(file: str | None, mic: bool, device: str | None) -> tuple[str | None, bool]:
    """(name of the device the listen side records, known). None for a file. Loopback capture records the default
    speaker unless --capture-device names another one. known=True only when the name is the device the recorder
    really opens: a microphone substring (--capture-device "Output") is resolved the way MicSource resolves it
    (#710 review: "Output" must be seen as "CABLE Output (VB-Audio ...)", not as a harmless word)."""
    if file:
        return None, False
    try:
        import soundcard as sc
    except Exception:  # noqa: BLE001 - no audio stack: the explicit-name checks still apply
        return device, False
    try:
        if device and mic:
            from .capture._soundcard import find_by_name

            mon = device.strip().casefold().startswith("monitor of")  # same rule as capture.mic.MicSource
            return find_by_name(sc, device, include_loopback=mon).name, True
        if device:
            return device, False
        return (sc.default_microphone() if mic else sc.default_speaker()).name, True
    except Exception:  # noqa: BLE001 - no such device / no audio stack: the name stays unproven
        return device, False


def capture_device_name(file: str | None, mic: bool, device: str | None) -> str | None:
    """Name of the device the listen side records (None for a file); see resolve_capture()."""
    return resolve_capture(file, mic, device)[0]


def open_voice(a, mdir, inner: Sink, say, off=None) -> tuple[VoiceSink, callable]:
    """Build the voice for parsed args `a` (`--voice`): Kokoro worker + player on a safe output device.
    off(str): called once if the voice turns itself off mid-run (GUI notice, #448).
    Returns (sink wrapping `inner`, close()). Raises RuntimeError with a one-line reason."""
    from .reverse.proto import Player
    from .reverse.tts_client import TTSClient

    lang = voice_lang(a.to)
    if lang is None:
        raise VoiceUnavailable(f"--voice speaks {', '.join(VOICE_LANGS)} only (Kokoro voices); --to is {a.to!r}",
                               "voice_lang_unsupported")
    try:
        player = Player(a.voice_device, 48000)
    except LookupError as e:  # no output device matches --voice-device
        raise VoiceUnavailable(f"--voice-device: {e}", "voice_device_missing") from e
    cap, known = resolve_capture(a.file, a.mic, a.device)
    # #710: the cable exception needs the microphone the recorder really opens; an unproven name never unlocks it
    mic_capture = bool(a.mic) and not a.file and known
    problem = voice_output_problem(player.speaker_name, cap, mic=mic_capture)
    if problem:
        player.close()
        raise VoiceUnavailable(f"--voice: {problem}. Pick earphones with --voice-device NAME", "voice_same_device")
    py, model = kokoro_paths(mdir, a.tts_python, a.tts_model)
    try:
        tts = TTSClient(py, model, "kokoro", a.voice_speaker, lang)
    except (OSError, RuntimeError, ValueError) as e:  # ValueError: garbled ready header (worker already killed)
        player.close()
        raise VoiceUnavailable(f"--voice: Kokoro voice not available ({e}); run tlwu-setup --talk",
                               "voice_not_installed") from e
    say(f"tlwu: voice ({lang}, Kokoro) on {player.speaker_name}")
    sink = VoiceSink(inner, make_speaker(tts, player, on_off=off), on_error=lambda e: say(f"tlwu: voice error: {e}"))
    if _virtual(" ".join(player.speaker_name.casefold().split())):  # #710: voice goes into a meeting cable
        sink.capture_guard = cable_guard(sink, player.speaker_name, say, off)

    def close(drop: bool = False) -> None:
        """drop=True on Ctrl+C / Stop: skip the waiting lines instead of speaking them first."""
        done = sink.close(drop=drop)
        if sink.dropped:
            say(f"tlwu: voice skipped {sink.dropped} line(s) to keep up with live speech (subtitles have them)")
        if not done:
            say(f"tlwu: voice still speaking at exit ({sink.pending()} line(s) left unspoken); left it running")
            return
        player.close()
        tts.close()

    return sink, close
