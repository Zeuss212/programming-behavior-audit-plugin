import asyncio
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
