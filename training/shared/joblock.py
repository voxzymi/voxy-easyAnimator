"""One training job at a time (the 12 GB card cannot hold two). A lock file with the owner's pid;
a lock left behind by a process that no longer exists is taken over."""
from __future__ import annotations

import os
import sys
from typing import Optional


def _alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform.startswith("win"):
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x00100000, False, pid)
        if not h:
            return False
        try:
            return ctypes.windll.kernel32.WaitForSingleObject(h, 0) == 0x102
        finally:
            ctypes.windll.kernel32.CloseHandle(h)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


class TrainingBusy(RuntimeError):
    pass


class JobLock:
    def __init__(self, path: str = "models/.training.lock", what: str = ""):
        self.path, self.what = path, what
        self.held = False

    def acquire(self) -> "JobLock":
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                owner: Optional[str] = None
                try:
                    txt = open(self.path).read().split("|", 1)
                    if _alive(int(txt[0])):
                        owner = txt[1] if len(txt) > 1 else "another job"
                except (ValueError, OSError):
                    pass
                if owner is not None:
                    raise TrainingBusy(f"a training job is already running ({owner.strip()}); run one at a time")
                try:
                    os.remove(self.path)          # stale lock from a dead process
                except OSError:
                    pass
                continue
            with os.fdopen(fd, "w") as f:
                f.write(f"{os.getpid()}|{self.what}")
            self.held = True
            return self
        raise TrainingBusy("could not take the training lock")

    def release(self) -> None:
        if self.held:
            try:
                os.remove(self.path)
            except OSError:
                pass
            self.held = False

    def __enter__(self) -> "JobLock":
        return self.acquire()

    def __exit__(self, *exc) -> None:
        self.release()
