"""Small stoppable janitor for stale collecting sessions."""

from __future__ import annotations

import math
import os
import threading
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from .session_store import SessionStore


_STALE_SESSION_TIMEOUT_ENV = (
    "JUPYTERLAB_BEHAVIOR_AUDIT_STALE_SESSION_TIMEOUT_SEC"
)
_DEFAULT_STALE_SESSION_TIMEOUT = timedelta(minutes=30)
_MIN_STALE_SESSION_TIMEOUT_SECONDS = 300
_MAX_STALE_SESSION_TIMEOUT_SECONDS = 3600


def stale_session_timeout() -> timedelta:
    """Load a bounded timeout while preserving the 30-minute default."""

    configured = os.environ.get(_STALE_SESSION_TIMEOUT_ENV)
    if configured is None:
        return _DEFAULT_STALE_SESSION_TIMEOUT
    try:
        seconds = float(configured)
    except ValueError:
        return _DEFAULT_STALE_SESSION_TIMEOUT
    if (
        not math.isfinite(seconds)
        or seconds < _MIN_STALE_SESSION_TIMEOUT_SECONDS
        or seconds > _MAX_STALE_SESSION_TIMEOUT_SECONDS
    ):
        return _DEFAULT_STALE_SESSION_TIMEOUT
    return timedelta(seconds=seconds)


class SessionJanitor:
    """Abandon stale collecting sessions without finalizing or analyzing."""

    def __init__(
        self,
        session_store: SessionStore,
        *,
        interval_seconds: float = 60,
        timeout: timedelta = timedelta(minutes=30),
        now: Callable[[], datetime] | None = None,
        on_abandoned: Callable[[str], object] | None = None,
    ) -> None:
        if (
            not isinstance(interval_seconds, (int, float))
            or isinstance(interval_seconds, bool)
            or interval_seconds <= 0
        ):
            raise ValueError("interval_seconds must be positive.")
        if timeout <= timedelta(0):
            raise ValueError("timeout must be positive.")
        self._session_store = session_store
        self._interval_seconds = float(interval_seconds)
        self._timeout = timeout
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._on_abandoned = on_abandoned
        self._stop = threading.Event()
        self._state_lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._started = False

    def run_once(self, *, now: datetime | None = None) -> list[str]:
        observed_at = now if now is not None else self._now()
        if observed_at.tzinfo is None:
            raise ValueError("now must include a UTC offset.")
        abandoned = self._session_store.abandon_stale(
            now=observed_at,
            timeout=self._timeout,
        )
        if self._on_abandoned is not None:
            for session_id in abandoned:
                try:
                    self._on_abandoned(session_id)
                except Exception:
                    continue
        return abandoned

    def start(self) -> None:
        with self._state_lock:
            if self._started:
                return
            self._stop.clear()
            try:
                self.run_once()
                thread = threading.Thread(
                    target=self._run,
                    name="myextension-session-janitor",
                    daemon=True,
                )
                thread.start()
            except Exception:
                self._stop.set()
                self._thread = None
                self._started = False
                raise
            self._thread = thread
            self._started = True

    def _run(self) -> None:
        while not self._stop.wait(self._interval_seconds):
            try:
                self.run_once()
            except Exception:
                # A later pass may succeed; no sensitive exception is persisted.
                continue

    def shutdown(self) -> None:
        with self._state_lock:
            self._stop.set()
            thread = self._thread
            self._thread = None
            self._started = False
        if (
            thread is not None
            and thread.is_alive()
            and thread is not threading.current_thread()
        ):
            thread.join(timeout=min(self._interval_seconds, 1.0))
