"""#614: Settings > Add-ons: installed and available language packs with size, Add / Remove, and the one-time-internet
wording. The logic is in addons.py (no tkinter there); downloads run in the tlwu-setup child (core.run_pack_download)."""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from ..i18n import t
from ..models import models_dir
from . import addons
from .core import download_cmd, run_pack_download


class AddonsScreen:
    def __init__(self, master, gpu_present: bool = False, mdir=None, run=run_pack_download, import_zip=None):
        self.mdir, self.gpu, self.run, self.q = mdir or models_dir(), gpu_present, run, queue.Queue()
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
        ttk.Button(bar, text=t("addons_retry"), command=self.refresh).pack(side="left")
        ttk.Button(bar, text=t("addons_pick_zip"), command=self.pick_zip).pack(side="left", padx=6)
        ttk.Button(bar, text=t("addons_close"), command=w.destroy).pack(side="right")
        self.refresh()
        self.win.after(200, self._poll)

    def refresh(self):
        for c in self.body.winfo_children():
            c.destroy()
        try:
            rows = addons.local_rows(self.mdir, self.gpu)
        except Exception:  # noqa: BLE001 - unreadable models folder: plain message, no traceback
            rows = []
            self.msg.set(t("addons_offline"))
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
        rc = subprocess.run([*cmd[:cmd.index("--whisper")], "--import-pack", path, *cmd[cmd.index("--models-dir"):]]).returncode
        return t("addons_import_ok") if rc == 0 else t("addons_import_hint")

    def _poll(self):
        try:
            while True:
                ev = self.q.get_nowait()
                if ev[0] == "pack_line":
                    self.msg.set(ev[1])
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
