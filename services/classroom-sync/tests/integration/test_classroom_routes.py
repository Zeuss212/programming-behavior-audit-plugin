import asyncio
import gzip
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from classroom_sync.application import ClassroomServices
from classroom_sync.auth.fincolab import Principal, StudentChildExperiment
from classroom_sync.config import Settings
from classroom_sync.domain.schemas import ClassroomSchemaRegistry
from classroom_sync.errors import RosterConflictError, UpstreamContractError
from classroom_sync.main import create_app
from classroom_sync.models import AuditEvent, Base, ExperimentPlanBinding, StudentAssignment
from classroom_sync.services.assignments import AssignmentService
from classroom_sync.services.plans import PlanDraftInput, PlanService
from classroom_sync.services.sessions import PluginSessionService
from tests.integration.test_plan_assignment_flow import profile_draft


class FakeIdentityGateway:
    def __init__(self) -> None:
        self.teacher_checks: list[tuple[str, str, str]] = []
        self.student_checks: list[tuple[str, str]] = []

    def resolve_principal(self, bearer_token: str) -> Principal:
        if bearer_token == "teacher-token":
            return Principal("teacher-1", "teacher-a", bearer_token)
        if bearer_token == "student-token":
            return Principal("student-1", "student-a", bearer_token)
        raise AssertionError(f"Unexpected token: {bearer_token}")

    def require_teacher_owner(
        self, principal: Principal, space_id: str, experiment_id: str
    ) -> None:
        self.teacher_checks.append((principal.user_id, space_id, experiment_id))

    def require_student_member(self, principal: Principal, space_id: str) -> None:
        self.student_checks.append((principal.user_id, space_id))

    def list_student_children(
        self, principal: Principal, space_id: str, parent_algorithm_id: str
    ) -> tuple[StudentChildExperiment, ...]:
        assert principal.user_id == "teacher-1"
        assert (space_id, parent_algorithm_id) == ("space-1", "parent-1")
        return (StudentChildExperiment("student-1", "student-a", "child-1", "workbench-1"),)


class RecordingStorage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_bytes(self, key: str, body: bytes, *, content_type: str) -> None:
        assert content_type == "application/gzip"
        self.objects[key] = body


class RaisingRosterGateway(FakeIdentityGateway):
    def __init__(self, error: Exception) -> None:
        super().__init__()
        self.error = error

    def list_student_children(self, principal: Principal, space_id: str, parent_algorithm_id: str):
        raise self.error


class FailingAssignmentService:
    def sync_assignments(self, plan_version, roster):
        raise AssertionError("assignment sync must not run after roster failure")


def request(app, method: str, path: str, **kwargs: object) -> httpx.Response:
    async def send() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(send())


def test_teacher_publish_sync_and_student_acceptance_use_trusted_router_boundaries():
    """The browser only supplies a bearer; role and roster checks stay server-side."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    repository_root = Path(__file__).resolve().parents[4]
    schema_registry = ClassroomSchemaRegistry(repository_root / "contracts" / "classroom" / "v1")
    now = datetime(2026, 8, 12, 8, 0, tzinfo=UTC)
    identity_gateway = FakeIdentityGateway()
    application = create_app(
        Settings(database_url="sqlite://"),
        classroom_services=ClassroomServices(
            identity_gateway=identity_gateway,
            plan_service=PlanService(session_factory, schema_registry, clock=lambda: now),
            assignment_service=AssignmentService(session_factory, clock=lambda: now),
        ),
    )
    teacher_headers = {"Authorization": "Bearer teacher-token"}
    student_headers = {"Authorization": "Bearer student-token"}
    draft_response = request(
        application,
        "POST",
        "/v1/classroom/plans/drafts",
        headers=teacher_headers,
        json={
            "space_id": "space-1",
            "parent_algorithm_id": "parent-1",
            "title": "字典课堂练习",
            "profile": profile_draft("学生是否正确读取字典中的值？"),
            "scheduled_start_at": now.isoformat(),
            "scheduled_end_at": (now + timedelta(minutes=30)).isoformat(),
            "ai_policy": "prohibited",
        },
    )
    assert draft_response.status_code == 201
    draft_id = draft_response.json()["draft_id"]

    publish_response = request(
        application,
        "POST",
        f"/v1/classroom/plans/drafts/{draft_id}/publish",
        headers=teacher_headers,
    )
    assert publish_response.status_code == 200
    plan_version_id = publish_response.json()["plan_version_id"]

    sync_response = request(
        application,
        "POST",
        f"/v1/classroom/plans/{plan_version_id}/assignments/sync",
        headers=teacher_headers,
    )
    assert sync_response.status_code == 200
    assignment_id = sync_response.json()["assignments"][0]["assignment_id"]

    accept_response = request(
        application,
        "POST",
        f"/v1/classroom/student/assignments/{assignment_id}/accept",
        headers=student_headers,
    )
    assert accept_response.status_code == 200
    assert accept_response.json()["status"] == "ready"
    assert identity_gateway.teacher_checks == [
        ("teacher-1", "space-1", "parent-1"),
        ("teacher-1", "space-1", "parent-1"),
        ("teacher-1", "space-1", "parent-1"),
    ]
    assert identity_gateway.student_checks == [("student-1", "space-1")]


@pytest.mark.parametrize(
    ("error", "status", "retryable"),
    [
        (RosterConflictError("student_binding_username_mismatch"), 409, False),
        (UpstreamContractError("child_workbench_unverified"), 503, True),
    ],
)
def test_roster_failure_returns_before_assignment_sync_or_any_assignment_write(error, status, retryable):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    root = Path(__file__).resolve().parents[4]
    plan_service = PlanService(session_factory, ClassroomSchemaRegistry(root / "contracts" / "classroom" / "v1"), clock=lambda: datetime(2026, 8, 12, tzinfo=UTC))
    draft = plan_service.create_draft(PlanDraftInput("space-1", "parent-1", "t", profile_draft("q"), datetime(2026, 8, 12, tzinfo=UTC), datetime(2026, 8, 12, 1, tzinfo=UTC), "prohibited"), teacher_id="teacher-1")
    version = plan_service.publish_draft(draft.id, teacher_id="teacher-1")
    with session_factory() as session:
        audit_count_before = len(session.scalars(select(AuditEvent)).all())
    app = create_app(Settings(database_url="sqlite://"), classroom_services=ClassroomServices(identity_gateway=RaisingRosterGateway(error), plan_service=plan_service, assignment_service=FailingAssignmentService()))
    response = request(app, "POST", f"/v1/classroom/plans/{version.id}/assignments/sync", headers={"Authorization":"Bearer teacher-token", "X-Request-ID":"request-1"})
    assert response.status_code == status
    assert response.json()["error"] == {"code": error.code, "message": "课堂服务请求未能完成。", "retryable": retryable, "request_id":"request-1"}
    with session_factory() as session:
        assert session.scalars(select(ExperimentPlanBinding)).all() == []
        assert session.scalars(select(StudentAssignment)).all() == []
        assert len(session.scalars(select(AuditEvent)).all()) == audit_count_before


def test_service_error_logs_safe_diagnostic_context(caplog):
    error = UpstreamContractError("child_workbench_unverified")
    app = create_app(Settings(database_url="sqlite://"))

    @app.get("/failing-route")
    def failing_route():
        raise error

    caplog.set_level(logging.WARNING, logger="classroom_sync.main")
    response = request(
        app,
        "GET",
        "/failing-route",
        headers={"Authorization": "Bearer must-not-be-logged", "X-Request-ID": "support-123"},
    )

    assert response.status_code == 503
    record = next(record for record in caplog.records if record.message == "classroom_service_error")
    assert record.method == "GET"
    assert record.path == "/failing-route"
    assert record.status_code == 503
    assert record.error_code == "child_workbench_unverified"
    assert record.retryable is True
    assert record.request_id == "support-123"
    assert "must-not-be-logged" not in caplog.text


def test_student_launches_plugin_with_one_time_ticket_and_uploads_evidence():
    """The HTTP boundary preserves student and plugin authentication separation."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    repository_root = Path(__file__).resolve().parents[4]
    schema_registry = ClassroomSchemaRegistry(repository_root / "contracts" / "classroom" / "v1")
    now = datetime(2026, 8, 12, 8, 0, tzinfo=UTC)
    identity_gateway = FakeIdentityGateway()
    plan_service = PlanService(session_factory, schema_registry, clock=lambda: now)
    assignment_service = AssignmentService(session_factory, clock=lambda: now)
    application = create_app(
        Settings(database_url="sqlite://"),
        classroom_services=ClassroomServices(
            identity_gateway=identity_gateway,
            plan_service=plan_service,
            assignment_service=assignment_service,
            plugin_session_service=PluginSessionService(
                session_factory,
                storage=RecordingStorage(),
                plugin_jwt_secret="test-plugin-secret-012345678901234567",
                clock=lambda: now,
                schema_registry=schema_registry,
            ),
        ),
    )
    draft = plan_service.create_draft(
        PlanDraftInput(
            space_id="space-1",
            parent_algorithm_id="parent-1",
            title="字典课堂练习",
            profile=profile_draft("学生是否正确读取字典中的值？"),
            scheduled_start_at=now,
            scheduled_end_at=now + timedelta(minutes=30),
            ai_policy="prohibited",
        ),
        teacher_id="teacher-1",
    )
    plan_version = plan_service.publish_draft(draft.id, teacher_id="teacher-1")
    assignment = assignment_service.sync_assignments(
        plan_version,
        (StudentChildExperiment("student-1", "student-a", "child-1", "workbench-1"),),
    )[0]
    assignment = assignment_service.accept_assignment(assignment.id, student_id="student-1")

    launch_response = request(
        application,
        "POST",
        f"/v1/classroom/student/assignments/{assignment.id}/launch-ticket",
        headers={"Authorization": "Bearer student-token"},
    )
    assert launch_response.status_code == 201
    ticket = launch_response.json()["ticket"]

    register_response = request(
        application,
        "POST",
        "/v1/classroom/plugin/sessions/register",
        json={"ticket": ticket, "plugin_instance_id": "plugin-instance-a"},
    )
    assert register_response.status_code == 201
    credentials = register_response.json()
    assert credentials["assignment_id"] == assignment.id
    assert credentials["plan_id"] == plan_version.plan_id
    assert credentials["plan_version"] == 1
    assert credentials["profile"] == plan_version.profile
    assert credentials["scheduled_end_at"] == (now + timedelta(minutes=30)).isoformat()
    assert credentials["last_sync_at"] == now.isoformat()
    assert credentials["evidence_cutoff_at"] == (now + timedelta(minutes=45)).isoformat()
    plugin_headers = {"Authorization": f"Bearer {credentials['access_token']}"}

    refresh_response = request(
        application,
        "POST",
        f"/v1/classroom/plugin/sessions/{credentials['session_id']}/context/refresh",
        headers=plugin_headers,
    )
    assert refresh_response.status_code == 200
    refreshed = refresh_response.json()
    assert refreshed["session_id"] == credentials["session_id"]
    assert refreshed["profile"] == credentials["profile"]
    assert refreshed["scheduled_end_at"] == credentials["scheduled_end_at"]
    assert refreshed["last_sync_at"] == credentials["last_sync_at"]

    heartbeat_response = request(
        application,
        "POST",
        f"/v1/classroom/plugin/sessions/{credentials['session_id']}/heartbeat",
        headers=plugin_headers,
    )
    assert heartbeat_response.status_code == 200
    evidence_response = request(
        application,
        "PUT",
        f"/v1/classroom/plugin/sessions/{credentials['session_id']}/evidence/1",
        headers={
            **plugin_headers,
            "Content-Type": "application/gzip",
            "X-First-Event-Sequence": "1",
            "X-Last-Event-Sequence": "1",
        },
        content=gzip.compress(b'{"events":[{"sequence":1}]}'),
    )
    assert evidence_response.status_code == 201
    assert evidence_response.json()["sequence"] == 1
    missing_token_response = request(
        application,
        "POST",
        f"/v1/classroom/plugin/sessions/{credentials['session_id']}/heartbeat",
    )
    assert missing_token_response.status_code == 401
    schema_registry.validate("error", missing_token_response.json())
