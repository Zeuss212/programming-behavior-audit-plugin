"""Server-side classroom deadline trigger independent of the browser page."""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import datetime, timezone


class PlatformDeadlineWorker:
    """Call the shared submission coordinator once the persisted cutoff arrives."""

    def __init__(
        self,
        context_store,
        coordinator,
        *,
        now: Callable[[], datetime] | None = None,
        interval_seconds: float = 30,
    ) -> None:
        if (
            not isinstance(interval_seconds, (int, float))
            or isinstance(interval_seconds, bool)
            or interval_seconds <= 0
        ):
            raise ValueError("interval_seconds must be positive.")
        self._context_store = context_store
        self._coordinator = coordinator
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._interval_seconds = float(interval_seconds)
        self._stop = threading.Event()
        self._state_lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._started = False

    def run_once(self) -> list[object]:
        context = self._context_store.read_registered_context()
        if context is None:
            return []
        observed_at = self._as_utc(self._now(), field="clock result")
        cutoff_at = self._parse_time(context.evidence_cutoff_at)
        if observed_at < cutoff_at:
            return []
        return [
            self._coordinator.submit(
                context.session_id,
                reason="system_deadline",
                cutoff_at=cutoff_at,
            )
        ]

    def start(self) -> None:
        with self._state_lock:
            if self._started:
                return
            self._stop.clear()
            try:
                self.run_once()
                thread = threading.Thread(
                    target=self._run,
                    name="myextension-platform-deadline",
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
                # The coordinator has durable state; a later pass can retry safely.
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

    @staticmethod
    def _parse_time(value: object) -> datetime:
        if not isinstance(value, str) or not value:
            raise ValueError("evidence_cutoff_at must be an ISO-8601 timestamp.")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("evidence_cutoff_at must be an ISO-8601 timestamp.") from error
        return PlatformDeadlineWorker._as_utc(parsed, field="evidence_cutoff_at")

    @staticmethod
    def _as_utc(value: datetime, *, field: str) -> datetime:
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ValueError(f"{field} must include a timezone.")
        return value.astimezone(timezone.utc)
