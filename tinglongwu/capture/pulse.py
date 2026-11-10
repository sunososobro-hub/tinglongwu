import sys

from ._soundcard import SoundcardSource, find_by_name

# Sink names/descriptions that are not a real speaker: a leftover null sink (ours is tlwu_sink, see
# reverse/vmic.py; pactl's default description is "Null Output"), or other virtual/dummy outputs.
_VIRTUAL_SINK_HINTS = ("tlwu_", "null", "dummy", "virtual", "loopback")


def virtual_sink_warning(name: str | None, sink_id: str | None = None) -> str | None:
    """One-line warning if the default output looks virtual (other apps' audio then goes nowhere audible)."""
    text = f"{name or ''} {sink_id or ''}".casefold()
    if not any(h in text for h in _VIRTUAL_SINK_HINTS):
        return None
    label = name or sink_id
    return (f"warning: default output {label!r} looks like a virtual/null sink: subtitles work but you will hear "
            f"nothing. Switch with `pactl list sinks short` then `pactl set-default-sink <your speakers>`.")


class PulseMonitorSource(SoundcardSource):
    """Linux PipeWire/PulseAudio: monitor of the default sink (or --device substring).

    List candidates with `pactl list sources short` (names ending in .monitor).
    """

    def _pick_microphone(self, sc):
        if self.device:
            return find_by_name(sc, self.device)
        spk = sc.default_speaker()
        msg = virtual_sink_warning(spk.name, getattr(spk, "id", None))
        if msg:
            print(msg, file=sys.stderr, flush=True)
        return sc.get_microphone(spk.name, include_loopback=True)
