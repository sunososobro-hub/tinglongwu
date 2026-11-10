"""Developer update (#84): what `update.bat` runs, so a dev checkout updates with a double-click.

  [1/3] git: fast-forward the `dev` branch to origin/dev (stops on local changes, another branch, or a dev that has
        commits origin/dev does not have)
  [2/3] pip: same pinned install as install.ps1 step 3 (`requirements.txt`, then `-e . --no-deps`), so new
        dependencies such as sv-ttk arrive
  [3/3] start tlwu-gui from the same venv

Run with the venv's Python: `.venv\\Scripts\\python.exe -m tinglongwu_setup.update`. Exit code 0 = GUI started.
Network: `git fetch` and pip only (this is a setup tool, like tlwu-setup; the runtime package stays offline).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BRANCH = "dev"


def _run(args, cwd) -> tuple[int, str]:
    p = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True, encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout + p.stderr).strip()


def _pip(args, cwd) -> int:
    """pip with its own output on the console (progress stays visible)."""
    return subprocess.call([sys.executable, "-m", "pip", *args], cwd=str(cwd))


def gui_exe(python=None) -> Path:
    """tlwu-gui next to the venv's python (Scripts\\tlwu-gui.exe on Windows, bin/tlwu-gui elsewhere)."""
    d = Path(python or sys.executable).parent
    return d / ("tlwu-gui.exe" if os.name == "nt" else "tlwu-gui")


def _launch(exe: Path, cwd) -> None:
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen([str(exe)], cwd=str(cwd), creationflags=flags, close_fds=True)


def git_update(repo, run=_run, say=print) -> bool:
    """Fast-forward `dev` to origin/dev. False (with a reason printed) when it must not or cannot."""
    rc, top = run(["git", "rev-parse", "--show-toplevel"], repo)
    if rc:
        say("  not a git checkout: re-download the ZIP and run install.bat / 不是 git 下載的資料夾：請重新下載並執行 install.bat")
        return False
    rc, br = run(["git", "rev-parse", "--abbrev-ref", "HEAD"], repo)
    if rc or br != BRANCH:
        say(f"  on branch '{br}', not '{BRANCH}': run `git switch {BRANCH}` first / 目前不在 {BRANCH} 分支")
        return False
    rc, dirty = run(["git", "status", "--porcelain", "--untracked-files=no"], repo)
    if rc or dirty:
        say("  local changes, commit or discard them first / 有本機修改，請先提交或還原：")
        for line in dirty.splitlines()[:10]:
            say(f"    {line}")
        return False
    rc, old = run(["git", "rev-parse", "--short", "HEAD"], repo)
    rc, out = run(["git", "fetch", "origin", BRANCH], repo)
    if rc:
        say(f"  git fetch failed (network?) / 無法連線取得更新:\n    {out[-300:]}")
        return False
    rc, out = run(["git", "merge", "--ff-only", f"origin/{BRANCH}"], repo)
    if rc:
        say(f"  your {BRANCH} has commits that are not on origin/{BRANCH} (no fast-forward) / 本機 {BRANCH} 與遠端分岔：\n"
            f"    {out[-300:]}")
        return False
    rc, new = run(["git", "rev-parse", "--short", "HEAD"], repo)
    say(f"  already up to date ({new}) / 已是最新" if new == old else f"  updated {old} -> {new} / 已更新")
    return True


def pip_install(repo, pip=_pip, say=print) -> bool:
    req = Path(repo) / "requirements.txt"
    steps = ([["install", "-r", "requirements.txt"]] if req.is_file() else []) + [["install", "-e", ".", "--no-deps"]]
    for args in steps:
        if pip(args, repo):
            say(f"  pip {' '.join(args)} failed / 套件安裝失敗（請確認網路後重跑 update.bat）")
            return False
    say("  packages up to date / 套件已更新")
    return True


def main(repo=None, run=_run, pip=_pip, launch=_launch, exe=None, say=print) -> int:
    repo = Path(repo or Path(__file__).resolve().parent.parent)
    say("[1/3] git pull (dev) / 取得最新程式")
    if not git_update(repo, run, say):
        return 1
    say("[2/3] pip install / 安裝套件")
    if not pip_install(repo, pip, say):
        return 1
    say("[3/3] start tlwu-gui / 啟動圖形介面")
    exe = Path(exe) if exe else gui_exe()
    if not exe.is_file():
        say(f"  {exe} not found: run install.bat first / 找不到 tlwu-gui，請先執行 install.bat")
        return 1
    try:
        launch(exe, repo)
    except OSError as e:
        say(f"  could not start {exe.name}: {e} / 無法啟動")
        return 1
    say("  done / 完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
