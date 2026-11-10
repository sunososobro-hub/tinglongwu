"""Linux virtual microphone for reverse mode (PipeWire via pipewire-pulse, or PulseAudio).

Creates a null sink `tlwu_sink` and a source `tlwu_mic` remapped from its monitor; the Russian TTS audio is
played to `tlwu_sink`, and Google Meet / Zoom pick `tlwu_mic` as their microphone. The modules are unloaded on exit.
Windows keeps VB-CABLE; macOS is not handled here.  EXPERIMENTAL: written without a Linux machine to test on.
"""
from __future__ import annotations

import atexit
import subprocess
import sys

SINK = "tlwu_sink"
MIC = "tlwu_mic"


def is_linux() -> bool:
    return sys.platform.startswith("linux")


def load_commands(sink: str = SINK, mic: str = MIC) -> list[list[str]]:
    """The two `pactl load-module` calls (each prints the new module id on stdout)."""
    return [
        ["pactl", "load-module", "module-null-sink", f"sink_name={sink}",
         f"sink_properties=device.description={sink}"],
        ["pactl", "load-module", "module-remap-source", f"master={sink}.monitor", f"source_name={mic}",
         f"source_properties=device.description={mic}"],
    ]


def unload_command(module_id: str) -> list[str]:
    return ["pactl", "unload-module", str(module_id)]


class VirtualMic:
    """Context manager / explicit open()+close(); `run` is injectable for tests (default subprocess.run)."""

    def __init__(self, sink: str = SINK, mic: str = MIC, run=None):
        self.sink, self.mic = sink, mic
        self._run = run or (lambda cmd: subprocess.run(cmd, capture_output=True, text=True, timeout=15))
        self.ids: list[str] = []

    def open(self) -> "VirtualMic":
        try:
            for cmd in load_commands(self.sink, self.mic):
                r = self._run(cmd)
                if r.returncode != 0:
                    raise RuntimeError(f"{' '.join(cmd[:3])} failed: {(r.stderr or r.stdout or '').strip()}")
                self.ids.append(r.stdout.strip())
        except FileNotFoundError as e:  # pactl missing
            self.close()
            raise RuntimeError("pactl not found; install pulseaudio-utils (PipeWire: pipewire-pulse) ") from e
        except BaseException:
            self.close()
            raise
        atexit.register(self.close)
        return self

    def close(self) -> None:
        """Unload in reverse order (the remap source depends on the sink). Safe to call twice."""
        while self.ids:
            mid = self.ids.pop()
            try:
                self._run(unload_command(mid))
            except Exception:  # noqa: BLE001 - best effort on exit
                pass

    def __enter__(self) -> "VirtualMic":
        return self.open()

    def __exit__(self, *a) -> None:
        self.close()
