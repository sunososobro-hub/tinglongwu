"""First-run setup screen (tkinter): detect the GPU, pick a tier, download models with a progress bar.

The download runs in a CHILD process (`tlwu.exe --run-setup ...`), so the network code (tinglongwu_setup) never
shares a process with the offline runtime (tinglongwu). Progress = bytes on disk / expected bytes.
"""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from . import tiers


def worker_cmd(bundled: bool, mdir: Path, args: list[str]) -> list[str]:
    """Command line of the download child process."""
    base = [os.path.abspath(sys.argv[0]) if bundled else sys.executable]
    if not bundled:
        base += ["-m", "tlwu_app"]
    return [*base, "--run-setup", *args, "--models-dir", str(mdir)]


def selftest_cmd(bundled: bool, mdir: Path, tier: tiers.Tier) -> list[str]:
    """Command line of the self-test child run after the download (#99): `tlwu --selftest`, offline."""
    base = [os.path.abspath(sys.argv[0])] if bundled else [sys.executable, "-m", "tlwu_app"]
    cmd = [*base, "--selftest", "--model", tier.whisper, "--models-dir", str(mdir)]
    return cmd + (["--device", "cpu"] if tier.name == "cpu" else [])


def selftest_verdict(rc: int, tail: str) -> tuple[str, str | None]:
    """(status line, warning text or None) for the self-test child's exit code. Never blocks starting the app."""
    if rc == 0:
        return "下載完成，自我測試通過！", None
    if rc == 2 and "--selftest" in tail:  # this tlwu has no --selftest yet (argparse: unrecognized arguments)
        return "下載完成！", None
    return ("下載完成，但自我測試失敗（詳見 install.log）。",
            "模型已下載，但自我測試沒有通過，翻譯可能無法使用。\n已下載的檔案都保留；可用「回報問題」附上 install.log。\n\n"
            + (tail.strip() or f"結束碼 {rc}")[-400:])


def last_line(chunk: str) -> str:
    """Newest non-empty line of progress output (tqdm redraws with a carriage return)."""
    parts = [p.strip() for p in chunk.replace("\r", "\n").split("\n") if p.strip()]
    return parts[-1][:90] if parts else ""


class SetupWindow:
    def __init__(self, mdir: Path, bundled: bool, force_cpu: bool = False):
        self.mdir, self.bundled = Path(mdir), bundled
        self.ok = False
        self.phase = "download"  # then "selftest" (#99)
        self.proc: subprocess.Popen | None = None
        self.q: queue.Queue = queue.Queue()
        raw = None if force_cpu else tiers.probe_gpu()
        self.gpu = None if tiers.driver_too_old(raw) else raw  # too-old driver = CPU tier + update hint (#95)
        self.tier = tiers.choose_tier(self.gpu, force_cpu)
        self.base_bytes = 0
        self.root = tk.Tk()
        self.root.title("聽攏有 tinglongWu - 首次設定")
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self.cancel)
        f = ttk.Frame(self.root, padding=14)
        f.pack()
        ttk.Label(f, text="歡迎使用聽攏有（離線即時翻譯）", font=("Microsoft JhengHei UI", 13, "bold")).pack(anchor="w")
        ttk.Label(f, text="第一次使用需要下載語音與翻譯模型。下載完成後，翻譯全程不需要網路。",
                  wraplength=420).pack(anchor="w", pady=(4, 8))
        ttk.Label(f, text=tiers.detection_text(raw, force_cpu), wraplength=420).pack(anchor="w")
        self.v_plan = tk.StringVar()
        ttk.Label(f, textvariable=self.v_plan, wraplength=420, foreground="#1a4d8f").pack(anchor="w", pady=(4, 8))
        self.v_cpu = tk.BooleanVar(value=force_cpu or self.gpu is None)
        if self.gpu:
            ttk.Checkbutton(f, text="改用 CPU 模式（下載較少，但翻譯較慢）", variable=self.v_cpu,
                            command=self._replan).pack(anchor="w")
        self._replan()
        self.bar = ttk.Progressbar(f, length=420, maximum=100)
        self.bar.pack(pady=(10, 2))
        self.v_status = tk.StringVar(value="按「開始下載」。可隨時取消，之後會從中斷處繼續。")
        ttk.Label(f, textvariable=self.v_status, wraplength=420).pack(anchor="w")
        row = ttk.Frame(f)
        row.pack(fill="x", pady=(10, 0))
        self.btn = ttk.Button(row, text="開始下載", command=self.start)
        self.btn.pack(side="left")
        ttk.Button(row, text="取消", command=self.cancel).pack(side="right")
        ttk.Label(f, text=f"模型存放位置：{self.mdir}", wraplength=420, foreground="#666").pack(anchor="w", pady=(8, 0))

    def _replan(self):
        self.tier = tiers.choose_tier(self.gpu, self.v_cpu.get())
        self.v_plan.set(f"將下載：{self.tier.label_zh}\n預計下載量：{self.tier.size_text}")

    # -- download ------------------------------------------------------------------------------
    def start(self):
        self.btn.configure(state="disabled")
        self.mdir.mkdir(parents=True, exist_ok=True)
        self.base_bytes = tiers.dir_size(self.mdir)
        self.v_status.set("連線中…")
        cmd = worker_cmd(self.bundled, self.mdir, self.tier.downloader_args())
        threading.Thread(target=self._run, args=(cmd,), daemon=True).start()
        self.root.after(500, self.poll)

    def _run(self, cmd):
        try:
            env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
            self.proc = p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
                                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            tail = ""
            while True:
                b = p.stdout.read1(512) if hasattr(p.stdout, "read1") else p.stdout.read(512)
                if not b:
                    break
                txt = b.decode("utf-8", "replace")
                self.q.put(("line", last_line(txt)))
                tail = (tail + txt)[-600:]
            rc = p.wait()
            self.q.put(("done", rc, tail))
        except Exception as e:  # noqa: BLE001
            self.q.put(("done", -1, f"{type(e).__name__}: {e}"))

    def poll(self):
        try:
            while True:
                ev = self.q.get_nowait()
                if ev[0] == "line" and ev[1]:
                    self.v_status.set(ev[1])
                elif ev[0] == "done":
                    return self.finished(ev[1], ev[2])
        except queue.Empty:
            pass
        done = max(0, tiers.dir_size(self.mdir) - self.base_bytes)
        self.bar["value"] = tiers.progress_fraction(done, self.tier) * 100
        self.root.after(500, self.poll)

    def finished(self, rc: int, tail: str):
        if self.phase == "selftest":
            status, warning = selftest_verdict(rc, tail)
            self.v_status.set(status)
            if warning:
                messagebox.showwarning("自我測試失敗", warning)
            self.root.after(600, self.root.destroy)
            return
        if rc == 0 and tiers.models_ready(self.mdir):
            self.bar["value"] = 100
            self.ok = True
            self.phase = "selftest"
            self.v_status.set("下載完成，自我測試中（載入模型並翻譯一句測試句）…")
            cmd = selftest_cmd(self.bundled, self.mdir, self.tier)
            threading.Thread(target=self._run, args=(cmd,), daemon=True).start()
            self.root.after(500, self.poll)
            return
        self.bar["value"] = 0
        self.btn.configure(state="normal", text="重試")
        self.v_status.set("下載失敗（請確認網路連線後按「重試」，已下載的部分不會重來）。")
        messagebox.showerror("下載失敗", (tail.strip() or f"結束碼 {rc}")[-400:])

    def cancel(self):
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
        self.root.destroy()

    def run(self) -> bool:
        self.root.mainloop()
        return self.ok


def run_setup_window(mdir: Path, bundled: bool, force_cpu: bool = False) -> bool:
    return SetupWindow(mdir, bundled, force_cpu).run()
