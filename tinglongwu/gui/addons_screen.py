"""#614: Settings > Add-ons: installed and available language packs with size, Add / Remove, and the one-time-internet
wording. The logic is in addons.py (no tkinter there); downloads run in the tlwu-setup child (core.run_pack_download)."""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from ..models import models_dir
from . import addons
from .core import download_cmd, run_pack_download

IMPORT_HINT = "Offline? Pick a local models zip (made with: tlwu-setup --export-pack)."


class AddonsScreen:
    def __init__(self, master, gpu_present: bool = False, mdir=None, run=run_pack_download, import_zip=None):
        self.mdir, self.gpu, self.run, self.q = mdir or models_dir(), gpu_present, run, queue.Queue()
        self.import_zip = import_zip or self._import_zip
        self.busy = False
        self.win = w = tk.Toplevel(master)
        w.title("Add-ons")
        self.body = ttk.Frame(w, padding=10)
        self.body.pack(fill="both", expand=True)
        self.msg = tk.StringVar()
        ttk.Label(w, textvariable=self.msg, wraplength=460, padding=(10, 0, 10, 8)).pack(fill="x")
        bar = ttk.Frame(w, padding=(10, 0, 10, 10))
        bar.pack(fill="x")
        ttk.Button(bar, text="Retry", command=self.refresh).pack(side="left")
        ttk.Button(bar, text="Pick a local models zip", command=self.pick_zip).pack(side="left", padx=6)
        ttk.Button(bar, text="Close", command=w.destroy).pack(side="right")
        self.refresh()
        self.win.after(200, self._poll)

    def refresh(self):
        for c in self.body.winfo_children():
            c.destroy()
        try:
            rows = addons.local_rows(self.mdir, self.gpu)
        except Exception:  # noqa: BLE001 - unreadable models folder: plain message, no traceback
            rows = []
            self.msg.set(addons.OFFLINE)
        for r in rows:
            f = ttk.Frame(self.body)
            f.pack(fill="x", pady=3)
            state = "installed" if r["installed"] else r["note"]
            ttk.Label(f, text=f"{r['name']}  ({r['size']})", width=16).pack(side="left")
            ttk.Label(f, text=state, wraplength=300).pack(side="left", fill="x", expand=True)
            if r["installed"]:
                ttk.Button(f, text="Remove", command=lambda n=r["name"]: self.remove(n)).pack(side="right")
            else:
                ttk.Button(f, text="Add", command=lambda n=r["name"]: self.add(n)).pack(side="right")

    def add(self, pack: str):
        if self.busy:
            return  # one setup child at a time: two would rewrite models/manifest.json from the same snapshot
        self.busy = True
        self.msg.set(f"Downloading {pack} ...")
        threading.Thread(target=self.run, args=(pack, self.q.put), kwargs={"mdir": self.mdir}, daemon=True).start()

    def remove(self, pack: str):
        if messagebox.askyesno("Add-ons", f"Remove the {pack} pack? Adding it back needs internet once.", parent=self.win):
            addons.remove_pack(pack, self.mdir)
            still = any(r["installed"] for r in addons.local_rows(self.mdir) if r["name"] == pack)
            self.msg.set(f"{pack} still installed: its models are shared with another pack or could not be deleted."
                         if still else f"Removed {pack}.")
            self.refresh()

    def pick_zip(self):
        path = filedialog.askopenfilename(parent=self.win, filetypes=[("Models zip", "*.zip")])
        if path and not self.busy:
            self.busy = True
            self.msg.set("Importing ...")

            def work():  # off the Tk thread: a big zip must not freeze the window
                self.q.put(("import_done", self.import_zip(path)))

            threading.Thread(target=work, daemon=True).start()

    def _import_zip(self, path: str) -> str:
        import subprocess

        cmd = download_cmd("small", self.mdir)  # same bundled / source launcher and --models-dir as pack downloads
        rc = subprocess.run([*cmd[:cmd.index("--whisper")], "--import-pack", path, *cmd[cmd.index("--models-dir"):]]).returncode
        return "Installed from the zip." if rc == 0 else IMPORT_HINT

    def _poll(self):
        try:
            while True:
                ev = self.q.get_nowait()
                if ev[0] == "pack_line":
                    self.msg.set(ev[1])
                elif ev[0] == "pack_done":
                    self.busy = False
                    self.msg.set(f"{ev[1]} installed." if ev[2] == 0 else
                                 f"{ev[1]}: download failed. Check the connection, then press Add again.")
                    self.refresh()
                elif ev[0] == "import_done":
                    self.busy = False
                    self.msg.set(ev[1])
                    self.refresh()
        except queue.Empty:
            pass
        if self.win.winfo_exists():
            self.win.after(200, self._poll)
