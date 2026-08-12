"""Crash-recoverable local queue for classroom evidence uploads.

The browser first writes behaviour events into :mod:`session_store`.  This
outbox is the second durable boundary: a compressed evidence chunk is stored
locally before any attempt is made to send it to the classroom service.  A
server receipt is deliberately handled later by ``flush_once`` so that a
restart can safely replay an already received chunk through the service's
idempotent evidence endpoint.
"""

from __future__ import annotations

import base64
import json
import random
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from typing import Final, Protocol
from uuid import UUID

from .canonical_json import atomic_write_json
from .platform_client import EvidenceUploadReceipt, PlatformClientError
from .platform_context_store import RegisteredPlatformContext


OUTBOX_DIRECTORY_NAME: Final = "platform-outbox"
STATE_FILENAME: Final = "state.json"
ENTRY_SCHEMA_VERSION: Final = 1
STATE_SCHEMA_VERSION: Final = 1


class EvidenceOutboxIntegrityError(RuntimeError):
    """Raised when durable queue artifacts cannot be trusted."""


def _canonical_session_id(value: str) -> str:
    try:
        parsed = UUID(value)
    except (AttributeError, TypeError, ValueError) as error:
        raise ValueError("session_id must be a canonical UUID.") from error
    if str(parsed) != value:
        raise ValueError("session_id must be a canonical UUID.")
    return value


def _require_positive_int(value: object, *, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{field} must be a positive integer.")
    return value


def _require_aware_timestamp(value: datetime, *, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{field} must include a timezone.")
    return value


def _parse_aware_timestamp(value: object, *, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise EvidenceOutboxIntegrityError(
            f"Stored {field} must be an ISO-8601 timestamp."
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise EvidenceOutboxIntegrityError(
            f"Stored {field} must be an ISO-8601 timestamp."
        ) from error
    if parsed.tzinfo is None:
        raise EvidenceOutboxIntegrityError(
            f"Stored {field} must include a timezone."
        )
    return parsed


@dataclass(frozen=True)
class EvidenceChunk:
    """A compressed chunk prepared from canonical local behaviour events."""

    sequence: int
    first_event_sequence: int
    last_event_sequence: int
    body: bytes
    created_at: datetime

    def __post_init__(self) -> None:
        sequence = _require_positive_int(self.sequence, field="sequence")
        first = _require_positive_int(
            self.first_event_sequence,
            field="first_event_sequence",
        )
        last = _require_positive_int(
            self.last_event_sequence,
            field="last_event_sequence",
        )
        if last < first:
            raise ValueError("last_event_sequence must not precede first_event_sequence.")
        if not isinstance(self.body, bytes) or not self.body:
            raise ValueError("body must be non-empty bytes.")
        _require_aware_timestamp(self.created_at, field="created_at")
        object.__setattr__(self, "sequence", sequence)
        object.__setattr__(self, "first_event_sequence", first)
        object.__setattr__(self, "last_event_sequence", last)


@dataclass(frozen=True)
class OutboxEntry:
    """One locally durable upload candidate and its delivery state."""

    session_id: str
    sequence: int
    first_event_sequence: int
    last_event_sequence: int
    body: bytes
    content_sha256: str
    created_at: datetime
    state: str
    attempts: int
    next_retry_at: datetime | None


@dataclass(frozen=True)
class FlushReport:
    """Outcome of one bounded attempt to upload pending evidence."""

    attempted: int
    delivered: int
    deferred: int
    quarantined: int


class EvidenceDeliveryClient(Protocol):
    def upload_evidence(
        self,
        context: RegisteredPlatformContext,
        *,
        sequence: int,
        body: bytes,
        first_event_sequence: int,
        last_event_sequence: int,
    ) -> EvidenceUploadReceipt: ...

    def refresh(
        self, context: RegisteredPlatformContext
    ) -> RegisteredPlatformContext: ...


class EvidenceContextStore(Protocol):
    def read_registered_context(self) -> RegisteredPlatformContext | None: ...

    def save_registered_context(
        self, context: RegisteredPlatformContext
    ) -> RegisteredPlatformContext: ...


class EvidenceOutbox:
    """Persist pending evidence before any remote delivery attempt.

    ``root`` is the trusted behaviour-log root, not a browser-controlled
    location.  Individual envelopes are immutable; mutable delivery state is
    atomically written to one small ``state.json`` per monitor session.
    """

    def __init__(
        self,
        root: Path,
        *,
        client: EvidenceDeliveryClient | None = None,
        context_store: EvidenceContextStore | None = None,
        clock: Callable[[], datetime] | None = None,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        self._root = Path(root)
        self._outbox_root = self._root / OUTBOX_DIRECTORY_NAME
        self._client = client
        self._context_store = context_store
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._jitter = jitter

    def enqueue(self, session_id: str, chunk: EvidenceChunk) -> OutboxEntry:
        """Durably queue *chunk*, returning the existing entry on replay."""

        canonical_session_id = _canonical_session_id(session_id)
        if not isinstance(chunk, EvidenceChunk):
            raise TypeError("chunk must be an EvidenceChunk.")
        session_dir = self._session_directory(canonical_session_id)
        session_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        filename = self._entry_filename(chunk)
        path = session_dir / filename

        if path.exists():
            stored = self._read_entry(path)
            expected = self._entry_from_chunk(canonical_session_id, chunk, "pending", 0, None)
            if (
                stored.session_id != expected.session_id
                or stored.sequence != expected.sequence
                or stored.content_sha256 != expected.content_sha256
                or stored.body != expected.body
            ):
                raise EvidenceOutboxIntegrityError(
                    "Stored evidence entry does not match its deterministic path."
                )
            return self._with_state(stored, self._read_state(session_dir).get(filename))

        existing_for_sequence = list(session_dir.glob(f"{chunk.sequence:08d}-*.json"))
        if existing_for_sequence:
            raise EvidenceOutboxIntegrityError(
                "An evidence sequence already exists with different content."
            )

        entry = self._entry_from_chunk(canonical_session_id, chunk, "pending", 0, None)
        atomic_write_json(path, self._entry_payload(entry))
        state = self._read_state(session_dir)
        state[filename] = self._state_payload(entry)
        self._write_state(session_dir, state)
        return entry

    def recover_pending(self) -> list[OutboxEntry]:
        """Return entries needing delivery, including entries left before state write."""

        if not self._outbox_root.exists():
            return []
        if self._outbox_root.is_symlink() or not self._outbox_root.is_dir():
            raise EvidenceOutboxIntegrityError("Outbox root is not a safe directory.")

        recovered: list[OutboxEntry] = []
        for session_dir in sorted(self._outbox_root.iterdir(), key=lambda path: path.name):
            if session_dir.is_symlink() or not session_dir.is_dir():
                raise EvidenceOutboxIntegrityError(
                    "Outbox contains an unsafe session directory."
                )
            session_id = _canonical_session_id(session_dir.name)
            state = self._read_state(session_dir)
            changed = False
            for path in sorted(session_dir.glob("????????-*.json"), key=lambda item: item.name):
                entry = self._read_entry(path)
                if entry.session_id != session_id:
                    raise EvidenceOutboxIntegrityError(
                        "Stored evidence session does not match its directory."
                    )
                row = state.get(path.name)
                if row is None:
                    state[path.name] = self._state_payload(entry)
                    row = state[path.name]
                    changed = True
                recovered_entry = self._with_state(entry, row)
                if recovered_entry.state in {"pending", "deferred", "inflight"}:
                    recovered.append(recovered_entry)
            if changed:
                self._write_state(session_dir, state)
        return sorted(
            recovered,
            key=lambda entry: (entry.session_id, entry.sequence, entry.content_sha256),
        )

    def flush_once(self, limit: int = 20) -> FlushReport:
        """Deliver at most ``limit`` due chunks, retaining all source evidence.

        A successful remote write is marked delivered only after its receipt is
        durably recorded locally.  Thus a process death in between is safe: a
        later invocation resends the immutable chunk and the service returns
        its idempotent receipt.
        """

        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise ValueError("limit must be a positive integer.")
        if self._client is None or self._context_store is None:
            raise RuntimeError("Evidence delivery dependencies are not configured.")
        now = _require_aware_timestamp(self._clock(), field="clock result")
        context = self._context_store.read_registered_context()
        if context is None:
            return FlushReport(attempted=0, delivered=0, deferred=0, quarantined=0)

        attempted = 0
        delivered = 0
        deferred = 0
        quarantined = 0
        for entry in self.recover_pending():
            if attempted >= limit:
                break
            if entry.session_id != context.session_id:
                continue
            if entry.next_retry_at is not None and entry.next_retry_at > now:
                deferred += 1
                continue
            attempted += 1
            try:
                self._upload_entry(context, entry)
            except PlatformClientError as error:
                if str(error) == "platform_evidence_unauthorized":
                    try:
                        context = self._context_store.save_registered_context(
                            self._client.refresh(context)
                        )
                        self._upload_entry(context, entry)
                    except PlatformClientError as refresh_error:
                        if self._quarantine_if_permanent(entry, refresh_error):
                            quarantined += 1
                        else:
                            self._defer(entry, now)
                            deferred += 1
                        continue
                else:
                    if self._quarantine_if_permanent(entry, error):
                        quarantined += 1
                    else:
                        self._defer(entry, now)
                        deferred += 1
                    continue
            self._mark(entry, state="delivered", attempts=entry.attempts)
            delivered += 1
        return FlushReport(
            attempted=attempted,
            delivered=delivered,
            deferred=deferred,
            quarantined=quarantined,
        )

    def _session_directory(self, session_id: str) -> Path:
        return self._outbox_root / _canonical_session_id(session_id)

    def _upload_entry(
        self,
        context: RegisteredPlatformContext,
        entry: OutboxEntry,
    ) -> None:
        if self._client is None:
            raise RuntimeError("Evidence delivery client is not configured.")
        receipt = self._client.upload_evidence(
            context,
            sequence=entry.sequence,
            body=entry.body,
            first_event_sequence=entry.first_event_sequence,
            last_event_sequence=entry.last_event_sequence,
        )
        if (
            receipt.session_id != entry.session_id
            or receipt.sequence != entry.sequence
            or receipt.content_sha256 != entry.content_sha256
        ):
            raise PlatformClientError("platform_evidence_invalid_response")

    def _quarantine_if_permanent(
        self,
        entry: OutboxEntry,
        error: PlatformClientError,
    ) -> bool:
        if str(error) not in {
            "platform_evidence_conflict",
            "platform_evidence_invalid",
            "platform_evidence_invalid_response",
        }:
            return False
        self._mark(entry, state="quarantined", attempts=entry.attempts + 1)
        return True

    def _defer(self, entry: OutboxEntry, now: datetime) -> None:
        attempts = entry.attempts + 1
        delay_seconds = self._retry_delay_seconds(attempts)
        self._mark(
            entry,
            state="deferred",
            attempts=attempts,
            next_retry_at=now + timedelta(seconds=delay_seconds),
        )

    def _retry_delay_seconds(self, attempts: int) -> float:
        base = (1, 2, 4, 8, 16, 30)[min(attempts - 1, 5)]
        jitter = self._jitter()
        if not isinstance(jitter, (int, float)) or isinstance(jitter, bool):
            raise ValueError("jitter must return a number between zero and one.")
        bounded_jitter = min(1.0, max(0.0, float(jitter)))
        return base * (1 + (bounded_jitter * 0.2))

    def _mark(
        self,
        entry: OutboxEntry,
        *,
        state: str,
        attempts: int,
        next_retry_at: datetime | None = None,
    ) -> None:
        session_dir = self._session_directory(entry.session_id)
        states = self._read_state(session_dir)
        updated = OutboxEntry(
            session_id=entry.session_id,
            sequence=entry.sequence,
            first_event_sequence=entry.first_event_sequence,
            last_event_sequence=entry.last_event_sequence,
            body=entry.body,
            content_sha256=entry.content_sha256,
            created_at=entry.created_at,
            state=state,
            attempts=attempts,
            next_retry_at=next_retry_at,
        )
        states[self._entry_filename_from_entry(entry)] = self._state_payload(updated)
        self._write_state(session_dir, states)

    @staticmethod
    def _entry_filename_from_entry(entry: OutboxEntry) -> str:
        return f"{entry.sequence:08d}-{entry.content_sha256}.json"

    @staticmethod
    def _entry_filename(chunk: EvidenceChunk) -> str:
        return f"{chunk.sequence:08d}-{sha256(chunk.body).hexdigest()}.json"

    @staticmethod
    def _entry_from_chunk(
        session_id: str,
        chunk: EvidenceChunk,
        state: str,
        attempts: int,
        next_retry_at: datetime | None,
    ) -> OutboxEntry:
        return OutboxEntry(
            session_id=session_id,
            sequence=chunk.sequence,
            first_event_sequence=chunk.first_event_sequence,
            last_event_sequence=chunk.last_event_sequence,
            body=chunk.body,
            content_sha256=sha256(chunk.body).hexdigest(),
            created_at=chunk.created_at,
            state=state,
            attempts=attempts,
            next_retry_at=next_retry_at,
        )

    @staticmethod
    def _entry_payload(entry: OutboxEntry) -> dict[str, object]:
        return {
            "schema_version": ENTRY_SCHEMA_VERSION,
            "session_id": entry.session_id,
            "sequence": entry.sequence,
            "first_event_sequence": entry.first_event_sequence,
            "last_event_sequence": entry.last_event_sequence,
            "content_sha256": entry.content_sha256,
            "created_at": entry.created_at.isoformat(),
            "body_base64": base64.b64encode(entry.body).decode("ascii"),
        }

    @staticmethod
    def _state_payload(entry: OutboxEntry) -> dict[str, object]:
        return {
            "state": entry.state,
            "attempts": entry.attempts,
            "next_retry_at": (
                entry.next_retry_at.isoformat()
                if entry.next_retry_at is not None
                else None
            ),
        }

    @staticmethod
    def _read_json(path: Path) -> dict[str, object]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise EvidenceOutboxIntegrityError(
                f"{path.name} is not a valid JSON object."
            ) from error
        if not isinstance(value, dict):
            raise EvidenceOutboxIntegrityError(f"{path.name} is not a JSON object.")
        return value

    def _read_entry(self, path: Path) -> OutboxEntry:
        value = self._read_json(path)
        expected_keys = {
            "schema_version",
            "session_id",
            "sequence",
            "first_event_sequence",
            "last_event_sequence",
            "content_sha256",
            "created_at",
            "body_base64",
        }
        if set(value) != expected_keys or value.get("schema_version") != ENTRY_SCHEMA_VERSION:
            raise EvidenceOutboxIntegrityError("Stored evidence entry has invalid keys.")
        try:
            session_id = _canonical_session_id(value["session_id"])
            sequence = _require_positive_int(value["sequence"], field="sequence")
            first = _require_positive_int(
                value["first_event_sequence"], field="first_event_sequence"
            )
            last = _require_positive_int(
                value["last_event_sequence"], field="last_event_sequence"
            )
            if last < first:
                raise ValueError("last_event_sequence must not precede first_event_sequence.")
            content_sha256 = value["content_sha256"]
            if not isinstance(content_sha256, str) or len(content_sha256) != 64:
                raise ValueError("content_sha256 must be a SHA-256 digest.")
            body = base64.b64decode(value["body_base64"], validate=True)
            if not body or sha256(body).hexdigest() != content_sha256:
                raise ValueError("Stored evidence body does not match its digest.")
            created_at = _parse_aware_timestamp(value["created_at"], field="created_at")
        except (KeyError, TypeError, ValueError) as error:
            raise EvidenceOutboxIntegrityError(
                "Stored evidence entry is invalid."
            ) from error
        expected_name = f"{sequence:08d}-{content_sha256}.json"
        if path.name != expected_name:
            raise EvidenceOutboxIntegrityError(
                "Stored evidence entry filename does not match its content."
            )
        return OutboxEntry(
            session_id=session_id,
            sequence=sequence,
            first_event_sequence=first,
            last_event_sequence=last,
            body=body,
            content_sha256=content_sha256,
            created_at=created_at,
            state="pending",
            attempts=0,
            next_retry_at=None,
        )

    def _read_state(self, session_dir: Path) -> dict[str, dict[str, object]]:
        path = session_dir / STATE_FILENAME
        if not path.exists():
            return {}
        value = self._read_json(path)
        if set(value) != {"schema_version", "entries"} or value.get(
            "schema_version"
        ) != STATE_SCHEMA_VERSION:
            raise EvidenceOutboxIntegrityError("Outbox state has invalid keys.")
        entries = value.get("entries")
        if not isinstance(entries, dict):
            raise EvidenceOutboxIntegrityError("Outbox state entries must be an object.")
        parsed: dict[str, dict[str, object]] = {}
        for filename, row in entries.items():
            if not isinstance(filename, str) or not isinstance(row, dict):
                raise EvidenceOutboxIntegrityError("Outbox state entry is invalid.")
            parsed[filename] = dict(row)
        return parsed

    def _write_state(self, session_dir: Path, state: dict[str, dict[str, object]]) -> None:
        atomic_write_json(
            session_dir / STATE_FILENAME,
            {"schema_version": STATE_SCHEMA_VERSION, "entries": state},
        )

    @staticmethod
    def _with_state(
        entry: OutboxEntry,
        state: dict[str, object] | None,
    ) -> OutboxEntry:
        if state is None:
            return entry
        if set(state) != {"state", "attempts", "next_retry_at"}:
            raise EvidenceOutboxIntegrityError("Outbox entry state has invalid keys.")
        value = state["state"]
        attempts = state["attempts"]
        retry_at = state["next_retry_at"]
        if value not in {"pending", "inflight", "deferred", "delivered", "quarantined"}:
            raise EvidenceOutboxIntegrityError("Outbox entry state is invalid.")
        if not isinstance(attempts, int) or isinstance(attempts, bool) or attempts < 0:
            raise EvidenceOutboxIntegrityError("Outbox entry attempts is invalid.")
        next_retry_at = (
            None
            if retry_at is None
            else _parse_aware_timestamp(retry_at, field="next_retry_at")
        )
        return OutboxEntry(
            session_id=entry.session_id,
            sequence=entry.sequence,
            first_event_sequence=entry.first_event_sequence,
            last_event_sequence=entry.last_event_sequence,
            body=entry.body,
            content_sha256=entry.content_sha256,
            created_at=entry.created_at,
            state=value,
            attempts=attempts,
            next_retry_at=next_retry_at,
        )


class EvidenceOutboxWorker:
    """Run bounded outbox flushes without delaying Jupyter HTTP handlers."""

    def __init__(
        self,
        outbox: EvidenceOutbox,
        *,
        interval_seconds: float = 1,
    ) -> None:
        if (
            not isinstance(interval_seconds, (int, float))
            or isinstance(interval_seconds, bool)
            or interval_seconds <= 0
        ):
            raise ValueError("interval_seconds must be positive.")
        self._outbox = outbox
        self._interval_seconds = float(interval_seconds)
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._state_lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._started = False

    def start(self) -> None:
        """Recover prior work before creating the single background thread."""

        with self._state_lock:
            if self._started:
                return
            self._stop.clear()
            self._wake.clear()
            self._outbox.recover_pending()
            try:
                thread = threading.Thread(
                    target=self._run,
                    name="myextension-evidence-outbox",
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
            self._wake.set()

    def notify(self) -> None:
        """Ask the worker to flush promptly after a new local enqueue."""

        self._wake.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._outbox.flush_once()
            except Exception:
                # Durable entries remain on disk; the next wake or interval can retry.
                pass
            self._wake.wait(self._interval_seconds)
            self._wake.clear()

    def shutdown(self) -> None:
        with self._state_lock:
            self._stop.set()
            self._wake.set()
            thread = self._thread
            self._thread = None
            self._started = False
        if (
            thread is not None
            and thread.is_alive()
            and thread is not threading.current_thread()
        ):
            thread.join(timeout=min(self._interval_seconds, 1.0))
