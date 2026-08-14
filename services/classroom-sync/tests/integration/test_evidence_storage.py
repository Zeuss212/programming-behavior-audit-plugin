import gzip
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from classroom_sync.domain.schemas import ClassroomSchemaRegistry
from classroom_sync.errors import ConflictError, UpstreamUnavailableError, ValidationError
from classroom_sync.models import (
    Base,
    EvidenceChunk,
    ExperimentPlanBinding,
    PlanVersion,
    StudentAssignment,
)
from classroom_sync.services.sessions import PluginSessionService


class RecordingStorage:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.objects: dict[str, bytes] = {}

    def put_bytes(self, key: str, body: bytes, *, content_type: str) -> None:
        if self.fail:
            raise OSError("object store unavailable")
        assert content_type == "application/gzip"
        self.objects[key] = body


def seeded_service(storage: RecordingStorage):
    now = datetime(2026, 8, 12, 8, 0, tzinfo=UTC)
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory.begin() as session:
        session.add(
            ExperimentPlanBinding(
                id="binding-1",
                space_id="space-1",
                parent_algorithm_id="parent-1",
                plan_id="plan-1",
                plan_version=1,
                teacher_id="teacher-1",
                created_at=now,
                updated_at=None,
            )
        )
        session.add(
            PlanVersion(
                id="plan-version-1",
                plan_id="plan-1",
                profile_id="profile-1",
                version=1,
                source_draft_revision=0,
                space_id="space-1",
                parent_algorithm_id="parent-1",
                profile={"schema_version": 2, "title": "课堂测试方案"},
                content_hash="a" * 64,
                scheduled_start_at=now,
                scheduled_end_at=now + timedelta(minutes=30),
                ai_policy="prohibited",
                published_at=now,
                teacher_id="teacher-1",
            )
        )
        session.add(
            StudentAssignment(
                id="assignment-1",
                binding_id="binding-1",
                space_id="space-1",
                parent_algorithm_id="parent-1",
                child_algorithm_id="child-1",
                workbench_id="workbench-1",
                student_id="student-1",
                plan_id="plan-1",
                plan_version=1,
                status="ready",
                scheduled_start_at=now,
                scheduled_end_at=now + timedelta(minutes=30),
                accepted_at=now,
                created_at=now,
                updated_at=now,
            )
        )
    service = PluginSessionService(
        factory,
        storage=storage,
        plugin_jwt_secret="test-plugin-secret-012345678901234567",
        clock=lambda: now,
        schema_registry=ClassroomSchemaRegistry(
            Path(__file__).resolve().parents[4] / "contracts" / "classroom" / "v1"
        ),
    )
    ticket = service.issue_ticket("assignment-1")
    credentials = service.register(ticket.ticket, plugin_instance_id="plugin-a")
    return service, factory, credentials


def test_evidence_is_private_idempotent_and_conflicts_on_a_changed_sequence():
    """The same bytes may retry safely; a changed sequence cannot overwrite evidence."""
    storage = RecordingStorage()
    service, _, credentials = seeded_service(storage)
    body = gzip.compress(b'{"events":[{"sequence":1}]}')

    first = service.put_evidence_chunk(
        credentials.access_token,
        session_id=credentials.session_id,
        sequence=1,
        body=body,
        first_event_sequence=1,
        last_event_sequence=1,
    )
    repeated = service.put_evidence_chunk(
        credentials.access_token,
        session_id=credentials.session_id,
        sequence=1,
        body=body,
        first_event_sequence=1,
        last_event_sequence=1,
    )

    assert first.content_sha256 == sha256(body).hexdigest()
    assert repeated.id == first.id
    assert len(storage.objects) == 1

    with pytest.raises(ConflictError, match="evidence_sequence_conflict"):
        service.put_evidence_chunk(
            credentials.access_token,
            session_id=credentials.session_id,
            sequence=1,
            body=gzip.compress(b'{"events":[{"sequence":2}]}'),
            first_event_sequence=2,
            last_event_sequence=2,
        )


def test_evidence_limits_and_storage_failure_do_not_create_database_receipts():
    """Unsafe payloads and failed object writes never gain a durable evidence index."""
    storage = RecordingStorage(fail=True)
    service, factory, credentials = seeded_service(storage)

    with pytest.raises(ValidationError, match="evidence_compressed_too_large"):
        service.put_evidence_chunk(
            credentials.access_token,
            session_id=credentials.session_id,
            sequence=1,
            body=b"x" * (2 * 1024 * 1024 + 1),
            first_event_sequence=1,
            last_event_sequence=1,
        )

    with pytest.raises(UpstreamUnavailableError, match="evidence_storage_unavailable"):
        service.put_evidence_chunk(
            credentials.access_token,
            session_id=credentials.session_id,
            sequence=2,
            body=gzip.compress(b'{"events":[{"sequence":2}]}'),
            first_event_sequence=2,
            last_event_sequence=2,
        )

    with factory() as session:
        assert session.scalars(select(EvidenceChunk)).all() == []
