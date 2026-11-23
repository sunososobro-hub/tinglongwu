#!/usr/bin/env python3
"""mobile-apk.yml helper (#1738): put Git for Windows' bash first on the job PATH.

    python -I scripts/windows_git_bash.py

On the self-hosted Windows runner `shell: bash` resolved to the WSL stub
C:\\Users\\...\\AppData\\Local\\Microsoft\\WindowsApps\\bash.EXE, so the first bash step
exited 1 and checkout / flutter test never ran. The runner looks a step's shell up in the
GITHUB_PATH entries before the system PATH, so appending Git's bin dir there makes every
later `bash` step use Git Bash (awk, sha256sum, cygpath come with it).

Search order: the folder of `git` on PATH (Git\\cmd or Git\\bin -> Git\\bin), then
%ProgramFiles%, %ProgramW6432%, %ProgramFiles(x86)% and %LOCALAPPDATA%\\Programs (each + Git\\bin).
A bash.exe under a WindowsApps folder (the WSL stub) is never taken. Exit 1 when none is found.
"""
import os
import shutil
import sys
from pathlib import PureWindowsPath


def _is_stub(path):
    return "windowsapps" in [p.lower() for p in PureWindowsPath(path).parts]


def candidates(env, git_path):
    out = []
    if git_path:
        parent = PureWindowsPath(git_path).parent
        if parent.name.lower() in ("cmd", "bin"):
            out.append(str(parent.parent / "bin"))
        if parent.parent.name.lower() == "mingw64":   # Git\mingw64\bin\git.exe
            out.append(str(parent.parent.parent / "bin"))
    for var in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
        if env.get(var):
            out.append(str(PureWindowsPath(env[var]) / "Git" / "bin"))
    if env.get("LOCALAPPDATA"):
        out.append(str(PureWindowsPath(env["LOCALAPPDATA"]) / "Programs" / "Git" / "bin"))
    seen, uniq = set(), []
    for c in out:
        if c.lower() not in seen and not _is_stub(c):
            seen.add(c.lower())
            uniq.append(c)
    return uniq


def find_git_bash_dir(env, git_path, exists):
    for d in candidates(env, git_path):
        if exists(str(PureWindowsPath(d) / "bash.exe")):
            return d
    return None


def main(env=None, git_path=None, exists=os.path.isfile):
    env = os.environ if env is None else env
    git_path = shutil.which("git") if git_path is None else git_path
    d = find_git_bash_dir(env, git_path, exists)
    if d is None:
        print("Git for Windows bash.exe not found (looked in: %s); install Git for Windows on the runner"
              % ", ".join(candidates(env, git_path)), file=sys.stderr)
        return 1
    gh_path = env.get("GITHUB_PATH")
    if gh_path:
        with open(gh_path, "a", encoding="utf-8") as f:
            f.write(d + "\n")
    print("Git Bash: %s" % d)
    return 0


if __name__ == "__main__":
    sys.exit(main())
