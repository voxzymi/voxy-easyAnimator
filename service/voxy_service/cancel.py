"""Cooperative cancellation and per-step timeouts."""
from __future__ import annotations

import threading
import time
from typing import Callable, Optional, TypeVar

from .errors import Cancelled, VoxyError

T = TypeVar("T")


class CancelToken:
    def __init__(self) -> None:
        self._ev = threading.Event()
        self._hooks: list[Callable[[], None]] = []
        self._lock = threading.Lock()

    def cancel(self) -> None:
        self._ev.set()
        with self._lock:
            hooks = list(self._hooks)
        for h in hooks:
            try:
                h()
            except Exception:
                pass

    @property
    def cancelled(self) -> bool:
        return self._ev.is_set()

    def check(self, stage: str = "") -> None:
        if self._ev.is_set():
            raise Cancelled(stage)

    def on_cancel(self, fn: Callable[[], None]) -> None:
        """Register a hook (for example, kill a subprocess). Runs at once if already cancelled."""
        with self._lock:
            self._hooks.append(fn)
        if self._ev.is_set():
            fn()


def run_step(fn: Callable[[], T], *, stage: str, timeout_s: Optional[float], token: CancelToken) -> T:
    """Run ``fn`` in a worker thread; stop waiting after ``timeout_s`` or on cancel.

    Python cannot kill a thread, so on timeout the step is abandoned and the token is
    cancelled so cooperative code (and subprocess hooks) stop as soon as they can.
    """
    box: dict = {}

    def target() -> None:
        try:
            box["value"] = fn()
        except BaseException as e:  # noqa: BLE001 - re-raised in the caller
            box["error"] = e

    th = threading.Thread(target=target, name=f"voxy-step-{stage}", daemon=True)
    th.start()
    deadline = None if timeout_s is None else time.monotonic() + timeout_s
    while th.is_alive():
        th.join(0.05)
        if token.cancelled:
            raise Cancelled(stage)
        if deadline is not None and time.monotonic() > deadline:
            token.cancel()
            raise VoxyError("STEP_TIMEOUT", f"step '{stage}' exceeded {timeout_s}s", stage=stage)
    if "error" in box:
        raise box["error"]
    return box["value"]
