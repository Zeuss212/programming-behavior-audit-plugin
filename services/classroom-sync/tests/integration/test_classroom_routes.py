import asyncio
import gzip
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from classroom_sync.application import ClassroomServices
from classroom_sync.auth.fincolab import Principal, StudentChildExperiment
from classroom_sync.config import Settings
from classroom_sync.domain.schemas import ClassroomSchemaRegistry
from classroom_sync.main import create_app
from classroom_sync.models import Base
from classroom_sync.services.assignments import AssignmentService
from classroom_sync.services.plans import PlanService
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
    application = create_app(
        Settings(database_url="sqlite://"),
        classroom_services=ClassroomServices(
            identity_gateway=identity_gateway,
            plan_service=PlanService(session_factory, schema_registry, clock=lambda: now),
            assignment_service=AssignmentService(session_factory, clock=lambda: now),
            plugin_session_service=PluginSessionService(
                session_factory,
                storage=RecordingStorage(),
                plugin_jwt_secret="test-plugin-secret-012345678901234567",
                clock=lambda: now,
                schema_registry=schema_registry,
            ),
        ),
    )
    with session_factory.begin() as session:
        from classroom_sync.models import ExperimentPlanBinding, StudentAssignment

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

    launch_response = request(
        application,
        "POST",
        "/v1/classroom/student/assignments/assignment-1/launch-ticket",
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
    assert credentials["assignment_id"] == "assignment-1"
    assert credentials["plan_id"] == "plan-1"
    assert credentials["plan_version"] == 1
    assert credentials["evidence_cutoff_at"] == (now + timedelta(minutes=45)).isoformat()
    plugin_headers = {"Authorization": f"Bearer {credentials['access_token']}"}

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
