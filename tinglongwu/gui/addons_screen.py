"""#614: Settings > Add-ons: installed and available language packs with size, Add / Remove, and the one-time-internet
wording. The logic is in addons.py (no tkinter there); downloads run in the tlwu-setup child (core.run_pack_download)."""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from ..i18n import t
from ..models import models_dir
from . import addons, gpu_offer
from .core import download_cmd, run_pack_download


GPU_ROW = gpu_offer.GPU_ROW_NAME  # pack name of the GPU row's download events (not a language pack)


class AddonsScreen:
    def __init__(self, master, gpu_present: bool = False, mdir=None, run=run_pack_download, import_zip=None,
                 gpu_state=None):
        self.mdir, self.gpu, self.run, self.q = mdir or models_dir(), gpu_present, run, queue.Queue()
        self.gpu_lines: list[str] = []
        self.gpu_cached = gpu_state() if gpu_state else None  # #1525: (state, version); None = probe still running
        self.import_zip = import_zip or self._import_zip
        self.busy = False
        self.win = w = tk.Toplevel(master)
        w.title(t("addons_title"))
        self.body = ttk.Frame(w, padding=10)
        self.body.pack(fill="both", expand=True)
        self.msg = tk.StringVar()
        ttk.Label(w, textvariable=self.msg, wraplength=460, padding=(10, 0, 10, 8)).pack(fill="x")
        bar = ttk.Frame(w, padding=(10, 0, 10, 10))
        bar.pack(fill="x")
        ttk.Button(bar, text=t("addons_retry"), command=self.retry).pack(side="left")
        ttk.Button(bar, text=t("addons_pick_zip"), command=self.pick_zip).pack(side="left", padx=6)
        ttk.Button(bar, text=t("addons_close"), command=w.destroy).pack(side="right")
        self.refresh()
        if gpu_state is None:  # nvidia-smi can take seconds: never on the Tk thread
            self._probe_gpu()
        self.win.after(200, self._poll)

    def refresh(self):
        for c in self.body.winfo_children():
            c.destroy()
        try:
            rows = addons.local_rows(self.mdir, self.gpu)
        except Exception:  # noqa: BLE001 - unreadable models folder: plain message, no traceback
            rows = []
            self.msg.set(t("addons_offline"))
        self._gpu_row()
        for r in rows:
            f = ttk.Frame(self.body)
            f.pack(fill="x", pady=3)
            state = t("addons_installed") if r["installed"] else r["note"]
            ttk.Label(f, text=f"{r['name']}  ({r['size']})", width=16).pack(side="left")
            ttk.Label(f, text=state, wraplength=300).pack(side="left", fill="x", expand=True)
            if r["installed"]:
                ttk.Button(f, text=t("addons_remove"), command=lambda n=r["name"]: self.remove(n)).pack(side="right")
            else:
                ttk.Button(f, text=t("addons_add"), command=lambda n=r["name"]: self.add(n)).pack(side="right")

    def _probe_gpu(self):
        self.gpu_gen = gen = getattr(self, "gpu_gen", 0) + 1  # a late, older probe must not overwrite a newer one
        threading.Thread(target=lambda: self.q.put(("gpu_state", self._gpu_state(), gen)), daemon=True).start()

    def retry(self):
        """Retry also probes the GPU again (a first nvidia-smi timeout must not stick until the window reopens)."""
        self.gpu_cached = None
        self._probe_gpu()
        self.refresh()

    @staticmethod
    def _gpu_state() -> tuple[str, str | None]:
        from ..gpu_pack import pack_dir

        try:
            state = gpu_offer.detect()
        except Exception:  # noqa: BLE001 - no probe: show the row as not available, never a traceback
            state = gpu_offer.UNAVAILABLE
        return state, gpu_offer.installed_version(pack_dir())

    def _gpu_row(self):
        """#1525: "GPU speed-up pack (NVIDIA, 1.5 GB)" with Install / Installed vX.Y.Z / Not available (no NVIDIA)."""
        if self.gpu_cached is None:
            return  # drawn once the background probe answers
        state, ver = self.gpu_cached
        f = ttk.Frame(self.body)
        f.pack(fill="x", pady=3)
        ttk.Label(f, text=t("gpu_pack_row_name", size=gpu_offer.GPU_PACK_SIZE)).pack(side="top", anchor="w")
        ttk.Label(f, text=gpu_offer.row_text(state, ver), wraplength=300).pack(side="left", fill="x", expand=True)
        if state in (gpu_offer.MISSING, gpu_offer.OLD):
            ttk.Button(f, text=t("gpu_offer_install"), command=self.add_gpu).pack(side="right")
        self.gpu_row_state = state

    def add_gpu(self):
        if self.busy or gpu_offer.INSTALLING.is_set():
            return
        if gpu_offer.offline():
            self.msg.set(t("addons_offline"))
            return
        from ..gpu_pack import pack_dir

        pack = pack_dir()
        if pack is not None and gpu_offer._pack_usable(pack):  # installed meanwhile (e.g. from the banner)
            self.gpu_cached = (gpu_offer.INSTALLED, gpu_offer.installed_version(pack))
            self.refresh()
            return
        gpu_offer.INSTALLING.set()
        self.busy = True
        self.gpu_lines = []
        self.msg.set(t("gpu_offer_running", size=gpu_offer.GPU_PACK_SIZE))
        try:
            threading.Thread(target=gpu_offer.run_install, args=(self.run, self.q.put),
                             kwargs={"mdir": self.mdir, "cmd": gpu_offer.gpu_pack_cmd(self.mdir)}, daemon=True).start()
        except RuntimeError as e:  # no thread: release the lock, keep the Install button usable
            gpu_offer.INSTALLING.clear()
            self.busy = False
            self.msg.set(t("gpu_offer_fail", reason=type(e).__name__))
            self.refresh()

    def add(self, pack: str):
        if self.busy:
            return  # one setup child at a time: two would rewrite models/manifest.json from the same snapshot
        self.busy = True
        self.msg.set(t("addons_downloading", pack=pack))
        threading.Thread(target=self.run, args=(pack, self.q.put), kwargs={"mdir": self.mdir}, daemon=True).start()

    def remove(self, pack: str):
        if messagebox.askyesno(t("addons_title"), t("addons_remove_confirm", pack=pack), parent=self.win):
            addons.remove_pack(pack, self.mdir)
            still = any(r["installed"] for r in addons.local_rows(self.mdir) if r["name"] == pack)
            self.msg.set(t("addons_still", pack=pack)
                         if still else t("addons_removed", pack=pack))
            self.refresh()

    def pick_zip(self):
        path = filedialog.askopenfilename(parent=self.win, filetypes=[(t("addons_zip_type"), "*.zip")])
        if path and not self.busy:
            self.busy = True
            self.msg.set(t("addons_importing"))

            def work():  # off the Tk thread: a big zip must not freeze the window
                try:
                    res = self.import_zip(path)
                except Exception as e:  # noqa: BLE001 - always report, or busy would stay set
                    res = t("addons_import_failed", err=type(e).__name__, hint=t("addons_import_hint"))
                self.q.put(("import_done", res))

            threading.Thread(target=work, daemon=True).start()

    def _import_zip(self, path: str) -> str:
        import subprocess

        cmd = download_cmd("small", self.mdir)  # same bundled / source launcher and --models-dir as pack downloads
        rc = subprocess.run([*cmd[:cmd.index("--whisper")], "--import-pack", path, *cmd[cmd.index("--models-dir"):]],
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).returncode
        return t("addons_import_ok") if rc == 0 else t("addons_import_hint")

    def _poll(self):
        try:
            while True:
                ev = self.q.get_nowait()
                if ev[0] == "pack_line":
                    self.msg.set(ev[1])
                    self.gpu_lines = [*self.gpu_lines[-20:], ev[1]]
                elif ev[0] == "gpu_state":
                    if len(ev) < 3 or ev[2] == self.gpu_gen:
                        self.gpu_cached = ev[1]
                        self.refresh()
                elif ev[0] == "pack_done" and ev[1] == GPU_ROW:  # #1525
                    self.busy = False
                    self.gpu_gen = getattr(self, "gpu_gen", 0) + 1  # drop probes started before the install ended
                    self.gpu_cached = self._gpu_state()  # the pack folder changed: re-read it
                    self.msg.set(t("gpu_offer_ok") if gpu_offer.install_ok(ev[2], self.gpu_lines) else
                                 t("gpu_offer_fail", reason=gpu_offer.fail_reason(self.gpu_lines)))
                    self.refresh()
                elif ev[0] == "pack_done":
                    self.busy = False
                    self.msg.set(t("addons_pack_ok", pack=ev[1]) if ev[2] == 0 else
                                 t("addons_pack_fail", pack=ev[1]))
                    self.refresh()
                elif ev[0] == "import_done":
                    self.busy = False
                    self.msg.set(ev[1])
                    self.refresh()
        except queue.Empty:
            pass
        if self.win.winfo_exists():
            self.win.after(200, self._poll)
