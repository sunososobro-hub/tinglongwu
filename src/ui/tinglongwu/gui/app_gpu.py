"""GPU speed-up pack offer line of the main window (#1525 / #1736 / #1775), split out of app.py (#1917).

`GpuOfferMixin` holds the App methods that build and drive the "NVIDIA GPU found - install speed-up pack?" line
and its Vulkan (AMD / Intel) variant; `App` inherits it, so every call site and test sees the same methods on `App`.
"""

from __future__ import annotations

import threading
import tkinter as tk
from tkinter import ttk

from . import core
from .app_update import UPDATE_LINE_WRAP_PX
from .core import save_settings


def _mtime(p):
    """#1525: identity of the installed GPU pack folder (a replacement gets a new mtime); None when absent."""
    try:
        return p.stat().st_mtime_ns if p is not None else None
    except OSError:
        return None



class GpuOfferMixin:
    """App methods for the GPU pack offer line (state lives on the App instance)."""

    # -- #1525: GPU speed-up pack offer ---------------------------------------------------------------
    def _build_gpu_offer(self):
        """One non-blocking line: "NVIDIA GPU found - install speed-up pack? [Install] [Not now] [Don't ask again]".
        Hidden unless gpu_offer.should_offer; nvidia-smi runs on a thread, never on the Tk thread."""
        from . import gpu_offer

        self.gpu_busy = False
        self.gpu_lines: list[str] = []
        g = self.gpu_frame = ttk.Frame(self.root, padding=(4, 0, 4, 2))
        self.v_gpu = tk.StringVar()
        _l = ttk.Label(g, textvariable=self.v_gpu, wraplength=UPDATE_LINE_WRAP_PX)
        _l.pack(side="left")
        # one line when the window is wide enough; wraps only as the width runs out (text + buttons ~ 300 px)
        g.bind("<Configure>", lambda e, l=_l: l.configure(wraplength=max(UPDATE_LINE_WRAP_PX, e.width - 300)), add="+")
        self.gpu_bar = ttk.Progressbar(g, mode="indeterminate", length=80)
        self.gpu_btns = [self._tx(ttk.Button(g, command=c, style="Bar.TButton", width=0), k) for k, c in
                         (("gpu_offer_never", self._gpu_never), ("gpu_offer_later", self._gpu_hide),
                          ("gpu_offer_install", self._gpu_install))]
        self.gpu_auto = None  # #1736: gpu_offer.AutoInstall while the background download runs
        self.gpu_cancel_btn = self._tx(ttk.Button(g, command=self._gpu_auto_cancel, style="Bar.TButton", width=0),
                                       "gpu_auto_cancel")
        probe = not gpu_offer.offline() and not gpu_offer.cpu_mode()

        def work():  # nvidia-smi once, off the Tk thread; also fills the CPU-mode wording cache (core.nvidia_seen)
            from .core import nvidia_seen

            nvidia_seen()
            if probe:
                st = self._gpu_detect()
                self.q.put(("gpu_state", st))
                if st == gpu_offer.UNAVAILABLE:  # #1775: no NVIDIA pack to offer -> maybe the Vulkan pack (AMD / Intel)
                    from . import vulkan_offer

                    self.q.put(("gpu_vk_state", vulkan_offer.gate_for_nvidia(self._vk_detect(), nvidia_seen())))

        threading.Thread(target=work, daemon=True).start()

    @staticmethod
    def _gpu_detect() -> str:
        from . import gpu_offer

        try:
            return gpu_offer.detect()
        except Exception:  # noqa: BLE001 - no probe: no banner
            return gpu_offer.UNAVAILABLE

    @staticmethod
    def _vk_detect() -> str:
        from . import vulkan_offer

        try:
            return vulkan_offer.detect()
        except Exception:  # noqa: BLE001 - no probe: no banner
            return vulkan_offer.UNAVAILABLE

    def _gpu_show(self, text: str, buttons: bool, busy: bool = False, cancel: bool = False):
        self.v_gpu.set(text)
        for b in self.gpu_btns:
            b.pack_forget()
        self.gpu_cancel_btn.pack_forget()
        if cancel:
            self.gpu_cancel_btn.pack(side="right", padx=(4, 0))
        if buttons:
            for b in self.gpu_btns:
                b.pack(side="right", padx=(4, 0))
        if busy:
            self.gpu_bar.pack(side="right", padx=(4, 0))
            self.gpu_bar.start(15)
        else:
            self.gpu_bar.stop()
            self.gpu_bar.pack_forget()
        if not self.gpu_frame.winfo_ismapped():
            self.gpu_frame.pack(fill="x")

    def _gpu_hide(self):
        self.gpu_bar.stop()
        self.gpu_frame.pack_forget()

    def _gpu_offer_show(self):
        from . import gpu_offer

        self._gpu_show(core.ui_text("gpu_offer_banner", size=gpu_offer.GPU_PACK_SIZE), buttons=True)

    def _gpu_never(self):
        from . import gpu_offer, vulkan_offer

        self.settings[vulkan_offer.DISMISS_KEY if getattr(self, "gpu_vk", False) else gpu_offer.DISMISS_KEY] = True
        save_settings(self.settings)
        self._gpu_hide()

    def _vk_install(self):
        """#1775: one click = Vulkan pack + whisper.cpp model + probe (vulkan_offer.install_cmds / run_install)."""
        from . import vulkan_offer
        from .core import run_pack_download

        bg = getattr(self, "bg_dl", None)
        if vulkan_offer.install_blocked(self.gpu_busy, bool(self.downloading), bool(bg is not None and bg.active)):
            return
        vulkan_offer.INSTALLING.set()
        self.gpu_busy, self.gpu_lines = True, []
        self._gpu_show(core.ui_text("vk_offer_running"), buttons=False, busy=True)
        put = lambda ev: self.q.put(("gpu_" + ev[0], *ev[1:]))  # noqa: E731
        try:
            threading.Thread(target=vulkan_offer.run_install, args=(run_pack_download, put, vulkan_offer.install_cmds()),
                             daemon=True).start()
        except RuntimeError:
            vulkan_offer.INSTALLING.clear()
            self.gpu_busy = False
            self._gpu_show(core.ui_text("vk_offer_banner"), buttons=True)

    def _gpu_auto_cancel(self):
        """#1736: Cancel on the background-download line: stop the child, remember it, show the one-click offer."""
        if self.gpu_auto is not None:
            self.gpu_auto.cancel()

    def _gpu_install(self, auto: bool = False):
        from . import gpu_offer
        from .core import run_pack_download

        if getattr(self, "gpu_vk", False):
            return self._vk_install()
        if self.gpu_busy or gpu_offer.INSTALLING.is_set() or gpu_offer.offline():
            return
        from ..gpu_pack import pack_dir

        pack = pack_dir()
        failed_at = getattr(self, "gpu_runtime_failed_mtime", None)  # pack replaced since the failure -> trust it
        if (pack is not None and (not getattr(self, "gpu_runtime_failed", False) or failed_at != _mtime(pack))
                and gpu_offer._pack_usable(pack)):  # installed meanwhile (e.g. Add-ons); not after a CUDA load failure
            self._gpu_show(core.ui_text("gpu_offer_ok"), buttons=False)
            return
        gpu_offer.INSTALLING.set()
        self.gpu_busy, self.gpu_lines = True, []
        put = lambda ev: self.q.put(("gpu_" + ev[0], *ev[1:]))  # noqa: E731 - not the language-pack events
        if auto:  # #1736: no click; one line with size + progress + Cancel, listening keeps running on the CPU
            self.gpu_auto = gpu_offer.AutoInstall(run_pack_download, self.settings, lambda: save_settings(self.settings))
            self._gpu_show(core.ui_text("gpu_auto_running", size=gpu_offer.GPU_PACK_SIZE), buttons=False, busy=True, cancel=True)
            target = self.gpu_auto.run_install
        else:
            self.gpu_auto = None
            self._gpu_show(core.ui_text("gpu_offer_running", size=gpu_offer.GPU_PACK_SIZE), buttons=False, busy=True)
            target = lambda put, **kw: gpu_offer.run_install(run_pack_download, put, **kw)  # noqa: E731
        try:
            threading.Thread(target=target, args=(put,), kwargs={"cmd": gpu_offer.gpu_pack_cmd()}, daemon=True).start()
        except RuntimeError:  # no thread: release the lock and offer Install again
            gpu_offer.INSTALLING.clear()
            self.gpu_busy = False
            self.gpu_auto = None
            self._gpu_offer_show()

    def _gpu_event(self, ev):
        from . import gpu_offer

        kind = ev[0]
        if kind == "gpu_state":
            if not self.gpu_busy and gpu_offer.should_offer(ev[1], gpu_offer.cpu_mode(), bool(self.settings.get(gpu_offer.DISMISS_KEY)),
                                      gpu_offer.offline()):
                if gpu_offer.should_autostart(ev[1], gpu_offer.cpu_mode() or not self._gpu_allowed(), bool(self.settings.get(gpu_offer.DISMISS_KEY)),
                                              gpu_offer.offline(), bool(self.settings.get(gpu_offer.AUTO_OFF_KEY))):
                    self._gpu_install(auto=True)  # #1736: download by default, the user can cancel
                else:
                    self._gpu_offer_show()
        elif kind == "gpu_vk_state":
            from . import vulkan_offer

            if not self.gpu_busy and vulkan_offer.should_offer(ev[1], False, bool(self.settings.get(vulkan_offer.DISMISS_KEY)),
                                                               gpu_offer.offline()):
                self.gpu_vk = True
                self._gpu_show(core.ui_text("vk_offer_banner"), buttons=True)
        elif kind == "gpu_pack_line":
            self.gpu_lines = [*self.gpu_lines[-20:], ev[1]]
        elif kind == "gpu_pack_done":
            self.gpu_busy = False
            auto, self.gpu_auto = self.gpu_auto, None
            if auto is not None and auto.outcome(ev[2], self.gpu_lines) == "cancelled":  # #1736: stays on CPU, offer remains
                self._gpu_offer_show()
            elif getattr(self, "gpu_vk", False):  # #1775: the Vulkan pack has its own wording (no CUDA restart notice)
                from . import vulkan_offer

                cpu = vulkan_offer.cpu_backends(self.gpu_lines) if ev[2] == 0 else []
                self._gpu_show(core.ui_text("vk_offer_fail", reason=gpu_offer.fail_reason(self.gpu_lines)) if ev[2] != 0
                               else core.ui_text("vk_offer_ok_cpu", cpu=" + ".join(cpu)) if cpu else core.ui_text("vk_offer_ok"), buttons=False)
            elif gpu_offer.install_ok(ev[2], self.gpu_lines):
                self.gpu_runtime_failed = False  # a fresh pack replaced the one that failed to load
                self._gpu_show(core.ui_text("gpu_offer_ok"), buttons=False)
            else:
                self._gpu_show(core.ui_text("gpu_offer_fail", reason=gpu_offer.fail_reason(self.gpu_lines)), buttons=False)
