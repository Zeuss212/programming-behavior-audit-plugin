from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.orm import Session, sessionmaker

from classroom_sync.domain.schemas import ClassroomSchemaRegistry
from classroom_sync.errors import AuthenticationError, AuthorizationError
from classroom_sync.models import (
    Base,
    ClassroomDeadlineJob,
    ExperimentPlanBinding,
    MonitorSession,
    PlanVersion,
    StudentAssignment,
)
from classroom_sync.services.sessions import PluginSessionService


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class DiscardingStorage:
    def put_bytes(self, key: str, body: bytes, *, content_type: str) -> None:
        del key, body, content_type


def session_factory_with_assignment(now: datetime) -> sessionmaker[Session]:
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        dbapi_connection.execute("PRAGMA foreign_keys = ON")

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
    return factory


def test_ticket_is_hashed_expires_and_can_only_be_consumed_once():
    """The plaintext launch ticket never enters storage and cannot be replayed."""
    clock = Clock(datetime(2026, 8, 12, 8, 0, tzinfo=UTC))
    factory = session_factory_with_assignment(clock())
    repository_root = Path(__file__).resolve().parents[4]
    service = PluginSessionService(
        factory,
        storage=DiscardingStorage(),
        plugin_jwt_secret="test-plugin-secret-012345678901234567",
        clock=clock,
        schema_registry=ClassroomSchemaRegistry(repository_root / "contracts" / "classroom" / "v1"),
    )

    issued = service.issue_ticket("assignment-1")
    with factory() as session:
        stored_ticket_hash = session.execute(text("SELECT ticket_hash FROM classroom_tickets")).scalar_one()

    assert issued.ticket not in stored_ticket_hash
    credentials = service.register(issued.ticket, plugin_instance_id="plugin-a")
    assert credentials.session_id
    with factory() as session:
        deadline_job = session.scalar(select(ClassroomDeadlineJob))
    assert deadline_job is not None
    assert deadline_job.status == "pending"
    assert deadline_job.run_at.replace(tzinfo=UTC) == clock() + timedelta(minutes=45)

    with pytest.raises(AuthorizationError, match="ticket_already_consumed"):
        service.register(issued.ticket, plugin_instance_id="plugin-b")


def test_new_ticket_resumes_the_existing_collecting_monitor_session():
    """A page reopen consumes a fresh ticket without creating a second session."""

    clock = Clock(datetime(2026, 8, 12, 8, 0, tzinfo=UTC))
    factory = session_factory_with_assignment(clock())
    repository_root = Path(__file__).resolve().parents[4]
    service = PluginSessionService(
        factory,
        storage=DiscardingStorage(),
        plugin_jwt_secret="test-plugin-secret-012345678901234567",
        clock=clock,
        schema_registry=ClassroomSchemaRegistry(
            repository_root / "contracts" / "classroom" / "v1"
        ),
    )

    first = service.register(
        service.issue_ticket("assignment-1").ticket,
        plugin_instance_id="plugin-a",
    )
    clock.now += timedelta(minutes=2)
    resumed = service.register(
        service.issue_ticket("assignment-1").ticket,
        plugin_instance_id="plugin-b",
    )

    assert resumed.session_id == first.session_id
    assert resumed.last_sync_at == clock()
    with factory() as session:
        assert len(session.scalars(select(MonitorSession)).all()) == 1
        assert len(session.scalars(select(ClassroomDeadlineJob)).all()) == 1


def test_expired_ticket_and_cross_session_plugin_token_are_rejected():
    """A plugin credential is short-lived and scoped to precisely one monitor session."""
    clock = Clock(datetime(2026, 8, 12, 8, 0, tzinfo=UTC))
    factory = session_factory_with_assignment(clock())
    repository_root = Path(__file__).resolve().parents[4]
    service = PluginSessionService(
        factory,
        storage=DiscardingStorage(),
        plugin_jwt_secret="test-plugin-secret-012345678901234567",
        clock=clock,
        schema_registry=ClassroomSchemaRegistry(repository_root / "contracts" / "classroom" / "v1"),
    )
    expired_ticket = service.issue_ticket("assignment-1")
    clock.now += timedelta(seconds=61)

    with pytest.raises(AuthenticationError, match="ticket_expired"):
        service.register(expired_ticket.ticket, plugin_instance_id="plugin-a")

    valid_ticket = service.issue_ticket("assignment-1")
    credentials = service.register(valid_ticket.ticket, plugin_instance_id="plugin-a")

    with pytest.raises(AuthorizationError, match="plugin_session_mismatch"):
        service.refresh_plugin_token(credentials.access_token, session_id="other-session")
