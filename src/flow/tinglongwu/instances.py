"""#1254: other tlwu instances on this PC and the CPU threads they use.

Two instances in CPU mode each planned threads for the whole CPU (owner field test: 16 threads on 12 CPUs), so
both got slower and dropped lines. Each CPU-mode instance now registers its ASR + MT threads in a per-user
directory; a later instance takes only what is left and prints one warning naming the other PIDs.

One `<pid>.json` (threads) and one `<pid>.lock` per instance. The instance holds an exclusive OS lock on its
`.lock` file while it runs; the OS drops the lock when the process ends (also on a crash or kill), so a file
whose lock can be taken is stale, whatever its PID is now used for.
"""
from __future__ import annotations

import contextlib
import json
import os
import stat
import sys
import tempfile
from dataclasses import dataclass

MIN_THREADS = 2  # a later instance still gets ASR 1 + MT 1 when the earlier ones already use every CPU


@dataclass(frozen=True)
class Other:
    pid: int
    threads: int


_held: dict = {}  # dir -> open lock file of this process


def instance_dir(env=None) -> str:
    """TLWU_INSTANCE_DIR, else a per-user directory under the temp dir."""
    env = os.environ if env is None else env
    d = (env.get("TLWU_INSTANCE_DIR") or "").strip()
    if d:
        return d
    who = str(os.getuid()) if hasattr(os, "getuid") else (env.get("USERNAME") or "user")
    return os.path.join(tempfile.gettempdir(), f"tlwu-instances-{who}")


_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


def _safe_dir(d: str) -> bool:
    """Create the registry directory private to this user. False (registry not used) when it is a symlink, owned
    by another user or writable by others: on a shared temp dir another account could pre-create it (CWE-59)."""
    try:
        os.makedirs(d, mode=0o700, exist_ok=True)
        st = os.lstat(d)
    except OSError:
        return False
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        return False
    if hasattr(os, "getuid") and (st.st_uid != os.getuid() or st.st_mode & 0o022):
        return False
    return True


def _open(path: str):
    """Open (create) a registry file for read/write without following a symlink at `path`."""
    return os.fdopen(os.open(path, os.O_RDWR | os.O_CREAT | _NOFOLLOW, 0o600), "r+b")


@contextlib.contextmanager
def allocating(env=None):
    """Hold the registry-wide allocation lock while reading others() and register()ing, so two instances that
    start at the same moment cannot both plan for the whole CPU. Without a usable directory: no lock."""
    d = instance_dir(env)
    f = None
    if _safe_dir(d):
        try:
            f = _open(os.path.join(d, "alloc.lock"))
            if sys.platform == "win32":
                import msvcrt

                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)  # retries for ~10 s, then OSError
            else:
                import fcntl

                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        except OSError:
            if f is not None:
                f.close()
            f = None
    try:
        yield
    finally:
        if f is not None:
            _unlock(f)
            f.close()


def _try_lock(f) -> bool:
    """Non-blocking exclusive lock on open file f; True when this process now holds it."""
    try:
        if sys.platform == "win32":
            import msvcrt

            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock(f) -> None:
    try:
        if sys.platform == "win32":
            import msvcrt

            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


def _remove(*paths) -> None:
    for p in paths:
        try:
            os.remove(p)
        except OSError:
            pass


def others(env=None) -> list[Other]:
    """Live instances other than this process, oldest PID first; stale files are removed."""
    d = instance_dir(env)
    if not _safe_dir(d):
        return []
    try:
        names = os.listdir(d)
    except OSError:
        return []
    found = []
    for name in names:
        if not name.endswith(".json") or not name[:-5].isdigit():
            continue
        pid = int(name[:-5])
        if pid == os.getpid():
            continue
        lock = os.path.join(d, f"{pid}.lock")
        data = os.path.join(d, name)
        try:
            with _open(lock) as f:
                if _try_lock(f):  # nobody holds it: that instance has ended
                    _unlock(f)
                    alive = False
                else:
                    alive = True
        except OSError:
            alive = False
        if not alive:
            _remove(data, lock)
            continue
        try:
            with os.fdopen(os.open(data, os.O_RDONLY | _NOFOLLOW), encoding="utf-8") as f:
                threads = int(json.load(f).get("threads", 0))
        except (OSError, ValueError, TypeError, AttributeError):
            continue
        found.append(Other(pid, max(0, threads)))
    return sorted(found, key=lambda o: o.pid)


def register(threads: int, env=None) -> bool:
    """Record this process's CPU threads (called again, it replaces the number). False when the directory or
    the lock is not usable; tlwu then runs as before, without telling other instances."""
    d = instance_dir(env)
    pid = os.getpid()
    if not _safe_dir(d):
        return False
    try:
        f = _held.get(d)
        if f is None:
            f = _open(os.path.join(d, f"{pid}.lock"))
            if not _try_lock(f):
                f.close()
                return False
            _held[d] = f
        tmp = os.path.join(d, f"{pid}.json.tmp")
        _remove(tmp)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump({"pid": pid, "threads": int(threads)}, out)
        os.replace(tmp, os.path.join(d, f"{pid}.json"))
        return True
    except OSError:
        return False


def release(env=None) -> None:
    """This process no longer uses the CPU threads it registered (session ended)."""
    d = instance_dir(env)
    f = _held.pop(d, None)
    pid = os.getpid()
    _remove(os.path.join(d, f"{pid}.json"))
    if f is not None:
        _unlock(f)
        f.close()
        _remove(os.path.join(d, f"{pid}.lock"))


def budget(cores: int, busy: list[Other]) -> int:
    """Threads left for this instance on `cores` CPUs next to `busy` (never below MIN_THREADS)."""
    used = sum(o.threads for o in busy)
    return max(MIN_THREADS, int(cores) - used)


def warning(cores: int, busy: list[Other], mine: int) -> str:
    """One stderr line naming the other instances' PIDs and the resulting total."""
    who = ", ".join(f"PID {o.pid} ({o.threads} threads)" for o in busy)
    total = sum(o.threads for o in busy) + mine
    over = " - CPU oversubscribed, close one instance" if total > cores else ""
    return (f"tlwu: warning: another tlwu is running on this CPU: {who}; this one uses {mine} threads, "
            f"{total} on {cores} CPUs{over}")


def release_all() -> None:
    """release() for every directory this process registered in (tests use one directory each)."""
    for d in list(_held):
        release({"TLWU_INSTANCE_DIR": d})
