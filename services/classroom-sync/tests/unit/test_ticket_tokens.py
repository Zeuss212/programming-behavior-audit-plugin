from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from classroom_sync.domain.schemas import ClassroomSchemaRegistry
from classroom_sync.errors import AuthenticationError, AuthorizationError
from classroom_sync.models import Base, ExperimentPlanBinding, StudentAssignment
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

    with pytest.raises(AuthorizationError, match="ticket_already_consumed"):
        service.register(issued.ticket, plugin_instance_id="plugin-b")


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
