"""Read-model HTTP contracts for the classroom teacher and student pages."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from classroom_sync.application import ClassroomServices
from classroom_sync.auth.fincolab import Principal, StudentChildExperiment
from classroom_sync.config import Settings
from classroom_sync.domain.schemas import ClassroomSchemaRegistry
from classroom_sync.errors import AuthorizationError
from classroom_sync.main import create_app
from classroom_sync.models import (
    Base,
    ExperimentPlanBinding,
    MonitorSession,
    PlanVersion,
    StudentAssignment,
    StudentBrief,
)
from classroom_sync.services.assignments import AssignmentService
from classroom_sync.services.briefs import BriefService
from classroom_sync.services.plans import PlanService
from classroom_sync.services.read_models import ClassroomReadService, monitoring_event_frame

NOW = datetime(2026, 8, 13, 8, 0, tzinfo=UTC)
PLAN_VERSION_ID = "11111111-1111-4111-8111-111111111111"
PLAN_ID = "22222222-2222-4222-8222-222222222222"
OWN_ASSIGNMENT_ID = "33333333-3333-4333-8333-333333333333"
OTHER_ASSIGNMENT_ID = "44444444-4444-4444-8444-444444444444"
OWN_SESSION_ID = "55555555-5555-4555-8555-555555555555"


class ReadModelIdentityGateway:
    """Trusted-identity double with a teacher and two independent students."""

    def resolve_principal(self, bearer_token: str) -> Principal:
        principals = {
            "teacher-token": Principal("teacher-1", "teacher-a", bearer_token),
            "student-token": Principal("student-1", "student-a", bearer_token),
            "other-student-token": Principal("student-2", "student-b", bearer_token),
        }
        return principals[bearer_token]

    def require_teacher_owner(
        self, principal: Principal, space_id: str, experiment_id: str
    ) -> None:
        if (principal.user_id, space_id, experiment_id) != ("teacher-1", "space-1", "parent-1"):
            raise AuthorizationError("teacher_experiment_owner_mismatch")

    def require_student_member(self, principal: Principal, space_id: str) -> None:
        if principal.user_id not in {"student-1", "student-2"} or space_id != "space-1":
            raise AuthorizationError("student_space_membership_required")

    def list_student_children(
        self, principal: Principal, space_id: str, parent_algorithm_id: str
    ) -> tuple[StudentChildExperiment, ...]:
        raise AssertionError((principal, space_id, parent_algorithm_id))


def request(app, method: str, path: str, **kwargs: object) -> httpx.Response:
    async def send() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(send())


@pytest.fixture()
def classroom_read_app():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory.begin() as session:
        session.add(
            PlanVersion(
                id=PLAN_VERSION_ID,
                plan_id=PLAN_ID,
                profile_id="profile-1",
                version=1,
                source_draft_revision=0,
                space_id="space-1",
                parent_algorithm_id="parent-1",
                profile={"title": "字典课堂练习", "knowledge_points": []},
                content_hash="a" * 64,
                scheduled_start_at=NOW,
                scheduled_end_at=NOW + timedelta(minutes=45),
                ai_policy="prohibited",
                published_at=NOW,
                teacher_id="teacher-1",
            )
        )
        session.add(
            ExperimentPlanBinding(
                id="binding-1",
                space_id="space-1",
                parent_algorithm_id="parent-1",
                plan_id=PLAN_ID,
                plan_version=1,
                teacher_id="teacher-1",
                created_at=NOW,
                updated_at=None,
            )
        )
        session.add_all(
            [
                StudentAssignment(
                    id=OWN_ASSIGNMENT_ID,
                    binding_id="binding-1",
                    space_id="space-1",
                    parent_algorithm_id="parent-1",
                    child_algorithm_id="child-1",
                    workbench_id="workbench-1",
                    student_id="student-1",
                    plan_id=PLAN_ID,
                    plan_version=1,
                    status="active",
                    scheduled_start_at=NOW,
                    scheduled_end_at=NOW + timedelta(minutes=45),
                    accepted_at=NOW,
                    created_at=NOW,
                    updated_at=NOW,
                ),
                StudentAssignment(
                    id=OTHER_ASSIGNMENT_ID,
                    binding_id="binding-1",
                    space_id="space-1",
                    parent_algorithm_id="parent-1",
                    child_algorithm_id="child-2",
                    workbench_id="workbench-2",
                    student_id="student-2",
                    plan_id=PLAN_ID,
                    plan_version=1,
                    status="pending_acceptance",
                    scheduled_start_at=NOW,
                    scheduled_end_at=NOW + timedelta(minutes=45),
                    accepted_at=None,
                    created_at=NOW,
                    updated_at=NOW,
                ),
            ]
        )
        session.add(
            MonitorSession(
                id=OWN_SESSION_ID,
                assignment_id=OWN_ASSIGNMENT_ID,
                plan_id=PLAN_ID,
                plan_version=1,
                status="completed",
                scheduled_end_at=NOW + timedelta(minutes=45),
                actual_end_at=NOW + timedelta(minutes=45),
                evidence_cutoff_at=NOW + timedelta(minutes=60),
                last_activity_at=NOW + timedelta(minutes=10),
                last_heartbeat_at=NOW + timedelta(minutes=10),
                last_contiguous_sequence=3,
                missing_ranges=[],
                completeness="complete",
                submission_reason="student_manual",
                active_slot=None,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.add(
            StudentBrief(
                id="brief-1",
                session_id=OWN_SESSION_ID,
                assignment_id=OWN_ASSIGNMENT_ID,
                revision=1,
                status="completed",
                data_completeness="complete",
                submission_reason="student_manual",
                payload={"summary": "学生完成字典读取练习。"},
                generated_at=NOW + timedelta(minutes=11),
            )
        )

    registry = ClassroomSchemaRegistry(
        Path(__file__).resolve().parents[4] / "contracts" / "classroom" / "v1"
    )
    return create_app(
        Settings(database_url="sqlite://"),
        classroom_services=ClassroomServices(
            identity_gateway=ReadModelIdentityGateway(),
            plan_service=PlanService(factory, registry, clock=lambda: NOW),
            assignment_service=AssignmentService(factory, clock=lambda: NOW),
            brief_service=BriefService(factory, registry, clock=lambda: NOW),
            read_service=ClassroomReadService(factory),
        ),
    )


def test_teacher_reads_own_experiment_plan_and_allowlisted_monitoring(classroom_read_app):
    """Replacing allowlisted DTOs with ORM state must fail this visible contract."""

    headers = {"Authorization": "Bearer teacher-token"}
    plan = request(
        classroom_read_app,
        "GET",
        "/v1/classroom/plans/experiments/space-1/parent-1",
        headers=headers,
    )
    monitoring = request(
        classroom_read_app,
        "GET",
        f"/v1/classroom/teacher/plans/{PLAN_VERSION_ID}/monitoring",
        headers=headers,
    )

    assert plan.status_code == 200
    assert plan.json() == {
        "plan_version_id": PLAN_VERSION_ID,
        "plan_id": PLAN_ID,
        "version": 1,
        "title": "字典课堂练习",
        "profile": {"title": "字典课堂练习", "knowledge_points": []},
        "scheduled_start_at": NOW.isoformat(),
        "scheduled_end_at": (NOW + timedelta(minutes=45)).isoformat(),
        "ai_policy": "prohibited",
        "published_at": NOW.isoformat(),
    }
    assert monitoring.status_code == 200
    assert monitoring.json()["students"] == [
        {
            "student_id": "student-1",
            "assignment_id": OWN_ASSIGNMENT_ID,
            "assignment_status": "active",
            "session": {
                "id": OWN_SESSION_ID,
                "status": "completed",
                "last_activity_at": (NOW + timedelta(minutes=10)).isoformat(),
                "submission_reason": "student_manual",
            },
            "brief": {
                "status": "completed",
                "revision": 1,
                "ai_analysis_status": "not_requested",
            },
        },
        {
            "student_id": "student-2",
            "assignment_id": OTHER_ASSIGNMENT_ID,
            "assignment_status": "pending_acceptance",
            "session": None,
            "brief": None,
        },
    ]
    rendered = monitoring.text
    for secret_field in ("ticket", "access_token", "object_key", "knowledge_point_reviews"):
        assert secret_field not in rendered


def test_student_reads_only_own_assignment_and_cannot_read_teacher_monitoring(classroom_read_app):
    """Changing the student filter or teacher check would expose another student's work."""

    headers = {"Authorization": "Bearer student-token"}
    listed = request(classroom_read_app, "GET", "/v1/classroom/student/assignments", headers=headers)
    own = request(
        classroom_read_app,
        "GET",
        f"/v1/classroom/student/assignments/{OWN_ASSIGNMENT_ID}",
        headers=headers,
    )
    other = request(
        classroom_read_app,
        "GET",
        f"/v1/classroom/student/assignments/{OTHER_ASSIGNMENT_ID}",
        headers=headers,
    )
    monitoring = request(
        classroom_read_app,
        "GET",
        f"/v1/classroom/teacher/plans/{PLAN_VERSION_ID}/monitoring",
        headers=headers,
    )

    expected = {
        "assignment_id": OWN_ASSIGNMENT_ID,
        "space_id": "space-1",
        "parent_algorithm_id": "parent-1",
        "child_algorithm_id": "child-1",
        "workbench_id": "workbench-1",
        "plan_id": PLAN_ID,
        "plan_version": 1,
        "title": "字典课堂练习",
        "profile": {"title": "字典课堂练习", "knowledge_points": []},
        "status": "active",
        "scheduled_start_at": NOW.isoformat(),
        "scheduled_end_at": (NOW + timedelta(minutes=45)).isoformat(),
        "session": {
            "id": OWN_SESSION_ID,
            "status": "completed",
            "last_activity_at": (NOW + timedelta(minutes=10)).isoformat(),
            "submission_reason": "student_manual",
        },
    }
    assert listed.status_code == 200
    assert listed.json() == {"assignments": [expected]}
    assert own.status_code == 200
    assert own.json() == expected
    assert other.status_code == 403
    assert other.json()["error"]["code"] == "student_assignment_owner_mismatch"
    assert monitoring.status_code == 403
    assert monitoring.json()["error"]["code"] == "teacher_experiment_owner_mismatch"


def test_monitoring_event_frame_and_empty_teacher_review_preserve_safety_boundaries(
    classroom_read_app,
):
    """An SSE frame stays credential-free and direct empty reviews are rejected."""

    frame = monitoring_event_frame({"plan_version_id": PLAN_VERSION_ID, "students": []})
    review = request(
        classroom_read_app,
        "POST",
        f"/v1/classroom/teacher/sessions/{OWN_SESSION_ID}/reviews",
        headers={"Authorization": "Bearer teacher-token"},
        json={"knowledge_point_reviews": [], "comment": ""},
    )

    assert frame == (
        "event: monitoring\n"
        'data: {"plan_version_id":"11111111-1111-4111-8111-111111111111","students":[]}\n\n'
    )
    assert "access_token" not in frame
    assert review.status_code == 422
