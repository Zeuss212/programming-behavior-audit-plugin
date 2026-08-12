"""One-time plugin registration and private, idempotent evidence persistence."""

from __future__ import annotations

import gzip
import secrets
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from io import BytesIO
from uuid import uuid4

import jwt
from jwt import ExpiredSignatureError, InvalidTokenError
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from classroom_sync.domain.schemas import ClassroomSchemaRegistry
from classroom_sync.errors import (
    AuthenticationError,
    AuthorizationError,
    ConflictError,
    NotFoundError,
    UpstreamUnavailableError,
    ValidationError,
)
from classroom_sync.models import (
    AuditEvent,
    ClassroomDeadlineJob,
    ClassroomTicket,
    EvidenceChunk,
    MonitorSession,
    StudentAssignment,
)
from classroom_sync.repositories import ClassroomRepository
from classroom_sync.storage import PrivateObjectStorage, StorageUnavailable

PLUGIN_TOKEN_AUDIENCE = "classroom-plugin-v1"
TICKET_TTL_SECONDS = 60
PLUGIN_TOKEN_TTL_SECONDS = 30 * 60
MAX_COMPRESSED_EVIDENCE_BYTES = 2 * 1024 * 1024
MAX_UNCOMPRESSED_EVIDENCE_BYTES = 10 * 1024 * 1024


@dataclass(frozen=True)
class IssuedTicket:
    ticket: str
    expires_at: datetime


@dataclass(frozen=True)
class SessionCredentials:
    session_id: str
    access_token: str
    expires_at: datetime
    assignment_id: str
    plan_id: str
    plan_version: int
    profile: dict[str, object]
    scheduled_end_at: datetime
    evidence_cutoff_at: datetime
    last_sync_at: datetime


@dataclass(frozen=True)
class EvidenceReceipt:
    id: str
    session_id: str
    sequence: int
    content_sha256: str
    object_key: str


class PluginSessionService:
    """Keep launch tickets one-use and evidence bodies outside PostgreSQL."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        storage: PrivateObjectStorage,
        plugin_jwt_secret: str,
        clock: Callable[[], datetime],
        schema_registry: ClassroomSchemaRegistry,
    ) -> None:
        self._session_factory = session_factory
        self._storage = storage
        self._plugin_jwt_secret = plugin_jwt_secret
        self._clock = clock
        self._schema_registry = schema_registry

    def issue_ticket(self, assignment_id: str) -> IssuedTicket:
        """Issue a 60-second ticket while storing only its SHA-256 hash."""

        now = self._utc_now()
        ticket = secrets.token_urlsafe(32)
        with self._session_factory.begin() as session:
            assignment = session.get(StudentAssignment, assignment_id)
            if assignment is None:
                raise NotFoundError("student_assignment_not_found")
            expires_at = now + timedelta(seconds=TICKET_TTL_SECONDS)
            session.add(
                ClassroomTicket(
                    id=str(uuid4()),
                    assignment_id=assignment.id,
                    ticket_hash=self._hash(ticket),
                    expires_at=expires_at,
                    consumed_at=None,
                    plugin_instance_hash=None,
                    created_at=now,
                )
            )
            self._audit(session, assignment.student_id, "plugin_ticket_issued", assignment.id, now)
        return IssuedTicket(ticket=ticket, expires_at=expires_at)

    def register(self, ticket: str, *, plugin_instance_id: str) -> SessionCredentials:
        """Consume a ticket atomically and return credentials scoped to the new session."""

        now = self._utc_now()
        with self._session_factory.begin() as session:
            ticket_record = session.scalar(
                select(ClassroomTicket)
                .where(ClassroomTicket.ticket_hash == self._hash(ticket))
                .with_for_update()
            )
            if ticket_record is None:
                raise AuthenticationError("ticket_invalid")
            if self._as_utc(ticket_record.expires_at) <= now:
                raise AuthenticationError("ticket_expired")
            if ticket_record.consumed_at is not None:
                raise AuthorizationError("ticket_already_consumed")

            assignment = session.get(StudentAssignment, ticket_record.assignment_id)
            if assignment is None:
                raise NotFoundError("student_assignment_not_found")
            if assignment.status not in {"ready", "active"}:
                raise AuthorizationError("assignment_not_ready_for_monitoring")
            plan_version = ClassroomRepository(session).get_plan_version(
                assignment.plan_id, assignment.plan_version
            )
            if plan_version is None:
                raise NotFoundError("plan_version_not_found")

            monitor_session = MonitorSession(
                id=str(uuid4()),
                assignment_id=assignment.id,
                plan_id=assignment.plan_id,
                plan_version=assignment.plan_version,
                status="collecting",
                scheduled_end_at=assignment.scheduled_end_at,
                actual_end_at=assignment.scheduled_end_at,
                evidence_cutoff_at=assignment.scheduled_end_at + timedelta(minutes=15),
                last_activity_at=now,
                last_heartbeat_at=now,
                last_contiguous_sequence=0,
                missing_ranges=[],
                completeness="complete",
                submission_reason=None,
                active_slot=1,
                created_at=now,
                updated_at=now,
            )
            assignment.status = "active"
            assignment.updated_at = now
            ticket_record.consumed_at = now
            ticket_record.plugin_instance_hash = self._hash(plugin_instance_id)
            session.add(monitor_session)
            session.add(
                ClassroomDeadlineJob(
                    id=str(uuid4()),
                    session_id=monitor_session.id,
                    run_at=monitor_session.evidence_cutoff_at,
                    status="pending",
                    lease_owner=None,
                    lease_expires_at=None,
                    attempts=0,
                    completed_at=None,
                    created_at=now,
                    updated_at=now,
                )
            )
            self._audit(session, assignment.student_id, "plugin_session_registered", monitor_session.id, now)

        return self._credentials_for(monitor_session, assignment, plan_version.profile, now)

    def refresh_plugin_token(self, access_token: str, *, session_id: str) -> SessionCredentials:
        """Refresh a plugin token only when it is still scoped to the requested session."""

        token_session_id = self._validate_plugin_token(access_token)
        if token_session_id != session_id:
            raise AuthorizationError("plugin_session_mismatch")
        with self._session_factory() as session:
            monitor_session = session.get(MonitorSession, session_id)
            if monitor_session is None:
                raise NotFoundError("monitor_session_not_found")
            assignment = session.get(StudentAssignment, monitor_session.assignment_id)
            if assignment is None:
                raise NotFoundError("student_assignment_not_found")
            plan_version = ClassroomRepository(session).get_plan_version(
                monitor_session.plan_id, monitor_session.plan_version
            )
            if plan_version is None:
                raise NotFoundError("plan_version_not_found")
        return self._credentials_for(
            monitor_session, assignment, plan_version.profile, self._utc_now()
        )

    def authorize_plugin_session(self, access_token: str, *, session_id: str) -> None:
        """Authorize an existing session without requiring a UI-context snapshot refresh."""

        if self._validate_plugin_token(access_token) != session_id:
            raise AuthorizationError("plugin_session_mismatch")
        with self._session_factory() as session:
            monitor_session = session.get(MonitorSession, session_id)
            if monitor_session is None:
                raise NotFoundError("monitor_session_not_found")
            if session.get(StudentAssignment, monitor_session.assignment_id) is None:
                raise NotFoundError("student_assignment_not_found")

    def heartbeat(self, access_token: str, *, session_id: str) -> MonitorSession:
        """Record liveness and resume a recoverable temporarily-offline session."""

        if self._validate_plugin_token(access_token) != session_id:
            raise AuthorizationError("plugin_session_mismatch")
        now = self._utc_now()
        with self._session_factory.begin() as session:
            monitor_session = session.get(MonitorSession, session_id)
            if monitor_session is None:
                raise NotFoundError("monitor_session_not_found")
            if monitor_session.status not in {"collecting", "temporarily_offline"}:
                raise AuthorizationError("monitor_session_not_collecting")
            monitor_session.status = "collecting"
            monitor_session.last_heartbeat_at = now
            monitor_session.updated_at = now
        return monitor_session

    def put_evidence_chunk(
        self,
        access_token: str,
        *,
        session_id: str,
        sequence: int,
        body: bytes,
        first_event_sequence: int,
        last_event_sequence: int,
    ) -> EvidenceReceipt:
        """Validate, privately store, and index one gzip evidence chunk exactly once."""

        if self._validate_plugin_token(access_token) != session_id:
            raise AuthorizationError("plugin_session_mismatch")
        if sequence < 1 or first_event_sequence < 1 or last_event_sequence < first_event_sequence:
            raise ValidationError("evidence_sequence_invalid")
        if len(body) > MAX_COMPRESSED_EVIDENCE_BYTES:
            raise ValidationError("evidence_compressed_too_large")
        uncompressed_bytes = self._validated_uncompressed_size(body)
        content_hash = sha256(body).hexdigest()
        now = self._utc_now()

        with self._session_factory.begin() as session:
            monitor_session = session.get(MonitorSession, session_id)
            if monitor_session is None:
                raise NotFoundError("monitor_session_not_found")
            if monitor_session.status not in {"collecting", "temporarily_offline"}:
                raise AuthorizationError("monitor_session_not_collecting")
            existing = session.scalar(
                select(EvidenceChunk).where(
                    EvidenceChunk.session_id == session_id,
                    EvidenceChunk.sequence == sequence,
                )
            )
            if existing is not None:
                if existing.content_sha256 == content_hash:
                    return self._receipt(existing)
                raise ConflictError("evidence_sequence_conflict")

            object_key = (
                f"classrooms/{monitor_session.plan_id}/sessions/{session_id}/chunks/"
                f"{sequence:08d}-{content_hash}.json.gz"
            )
            manifest = {
                "schema_version": 1,
                "session_id": session_id,
                "sequence": sequence,
                "content_sha256": content_hash,
                "content_encoding": "gzip",
                "media_type": "application/json",
                "compressed_bytes": len(body),
                "uncompressed_bytes": uncompressed_bytes,
                "first_event_sequence": first_event_sequence,
                "last_event_sequence": last_event_sequence,
                "object_key": object_key,
                "created_at": now.isoformat(),
            }
            self._schema_registry.validate("evidence-chunk-manifest", manifest)
            try:
                self._storage.put_bytes(object_key, body, content_type="application/gzip")
            except (OSError, StorageUnavailable) as error:
                raise UpstreamUnavailableError("evidence_storage_unavailable") from error

            evidence_chunk = EvidenceChunk(
                id=str(uuid4()),
                session_id=session_id,
                sequence=sequence,
                content_sha256=content_hash,
                content_encoding="gzip",
                media_type="application/json",
                compressed_bytes=len(body),
                uncompressed_bytes=uncompressed_bytes,
                first_event_sequence=first_event_sequence,
                last_event_sequence=last_event_sequence,
                object_key=object_key,
                created_at=now,
            )
            monitor_session.last_activity_at = now
            monitor_session.last_contiguous_sequence = max(
                monitor_session.last_contiguous_sequence, sequence
            )
            monitor_session.updated_at = now
            session.add(evidence_chunk)
            self._audit(session, None, "evidence_chunk_stored", evidence_chunk.id, now)
        return self._receipt(evidence_chunk)

    def _credentials_for(
        self,
        monitor_session: MonitorSession,
        assignment: StudentAssignment,
        profile: dict[str, object],
        now: datetime,
    ) -> SessionCredentials:
        expires_at = now + timedelta(seconds=PLUGIN_TOKEN_TTL_SECONDS)
        access_token = jwt.encode(
            {
                "sub": monitor_session.id,
                "aud": PLUGIN_TOKEN_AUDIENCE,
                "iat": int(now.timestamp()),
                "exp": int(expires_at.timestamp()),
            },
            self._plugin_jwt_secret,
            algorithm="HS256",
        )
        return SessionCredentials(
            session_id=monitor_session.id,
            access_token=access_token,
            expires_at=expires_at,
            assignment_id=assignment.id,
            plan_id=monitor_session.plan_id,
            plan_version=monitor_session.plan_version,
            profile=deepcopy(profile),
            scheduled_end_at=self._as_utc(monitor_session.scheduled_end_at),
            evidence_cutoff_at=self._as_utc(monitor_session.evidence_cutoff_at),
            last_sync_at=self._as_utc(
                monitor_session.last_heartbeat_at or monitor_session.updated_at
            ),
        )

    def _validate_plugin_token(self, access_token: str) -> str:
        try:
            payload = jwt.decode(
                access_token,
                self._plugin_jwt_secret,
                algorithms=["HS256"],
                audience=PLUGIN_TOKEN_AUDIENCE,
                options={"verify_exp": False, "verify_iat": False},
            )
        except ExpiredSignatureError as error:
            raise AuthenticationError("plugin_token_expired") from error
        except InvalidTokenError as error:
            raise AuthenticationError("plugin_token_invalid") from error
        subject = payload.get("sub")
        if not isinstance(subject, str) or not subject:
            raise AuthenticationError("plugin_token_invalid")
        expires_at = payload.get("exp")
        if not isinstance(expires_at, int) or expires_at <= int(self._utc_now().timestamp()):
            raise AuthenticationError("plugin_token_expired")
        return subject

    @staticmethod
    def _hash(value: str) -> str:
        return sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _receipt(evidence_chunk: EvidenceChunk) -> EvidenceReceipt:
        return EvidenceReceipt(
            id=evidence_chunk.id,
            session_id=evidence_chunk.session_id,
            sequence=evidence_chunk.sequence,
            content_sha256=evidence_chunk.content_sha256,
            object_key=evidence_chunk.object_key,
        )

    @staticmethod
    def _validated_uncompressed_size(body: bytes) -> int:
        try:
            with gzip.GzipFile(fileobj=BytesIO(body), mode="rb") as archive:
                size = 0
                while chunk := archive.read(min(64 * 1024, MAX_UNCOMPRESSED_EVIDENCE_BYTES + 1)):
                    size += len(chunk)
                    if size > MAX_UNCOMPRESSED_EVIDENCE_BYTES:
                        raise ValidationError("evidence_uncompressed_too_large")
        except (OSError, EOFError) as error:
            raise ValidationError("evidence_gzip_invalid") from error
        if size < 1:
            raise ValidationError("evidence_uncompressed_empty")
        return size

    def _utc_now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None:
            raise ValueError("Clock must return timezone-aware UTC datetimes.")
        return now.astimezone(timezone.utc)

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        """Normalize SQLite's naive timestamps while preserving PostgreSQL UTC instants."""

        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _audit(
        session: Session,
        actor_id: str | None,
        event_type: str,
        entity_id: str,
        created_at: datetime,
    ) -> None:
        session.add(
            AuditEvent(
                id=str(uuid4()),
                actor_id=actor_id,
                event_type=event_type,
                entity_type="monitoring",
                entity_id=entity_id,
                request_id=None,
                payload={},
                created_at=created_at,
            )
        )
