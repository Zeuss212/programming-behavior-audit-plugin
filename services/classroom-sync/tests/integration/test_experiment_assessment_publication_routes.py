"""Saving a valid experiment assessment must publish a usable classroom."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
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
from classroom_sync.models import Base, ExperimentPlanBinding, PlanVersion, StudentAssignment
from classroom_sync.services.assignments import AssignmentService
from classroom_sync.services.experiment_assessment_configs import ExperimentAssessmentConfigService
from classroom_sync.services.experiment_publications import ExperimentPublicationService
from classroom_sync.services.plan_authoring import PlanAuthoringService
from classroom_sync.services.plans import PlanService


class PublicationIdentityGateway:
    def resolve_principal(self, bearer_token: str) -> Principal:
        assert bearer_token == "teacher-token"
        return Principal("teacher-1", "teacher-a", bearer_token)

    def require_teacher_owner(
        self, principal: Principal, space_id: str, experiment_id: str
    ) -> None:
        assert (principal.user_id, space_id, experiment_id) == (
            "teacher-1",
            "course-1",
            "experiment-1",
        )

    def require_student_member(self, principal: Principal, space_id: str) -> None:
        raise AssertionError("student authorization is not expected")

    def list_student_children(
        self, principal: Principal, space_id: str, parent_algorithm_id: str
    ) -> tuple[StudentChildExperiment, ...]:
        assert (principal.user_id, space_id, parent_algorithm_id) == (
            "teacher-1",
            "course-1",
            "experiment-1",
        )
        return (
            StudentChildExperiment(
                student_id="student-1",
                student_username="student-a",
                child_algorithm_id="child-1",
                workbench_id="workbench-1",
            ),
        )


def request(app: object, method: str, path: str, **kwargs: object) -> httpx.Response:
    async def send() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(send())


def make_application() -> tuple[object, sessionmaker]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    now = datetime(2026, 9, 2, 8, 0, tzinfo=UTC)
    repository_root = Path(__file__).resolve().parents[4]
    plans = PlanService(
        factory,
        ClassroomSchemaRegistry(repository_root / "contracts" / "classroom" / "v1"),
        clock=lambda: now,
    )
    assignments = AssignmentService(factory, clock=lambda: now)
    authoring = PlanAuthoringService(factory, None, clock=lambda: now)
    services = ClassroomServices(
        identity_gateway=PublicationIdentityGateway(),
        plan_service=plans,
        assignment_service=assignments,
        plan_authoring_service=authoring,
        experiment_assessment_config_service=ExperimentAssessmentConfigService(
            factory, clock=lambda: now
        ),
        experiment_publication_service=ExperimentPublicationService(
            factory,
            plan_service=plans,
            plan_authoring_service=authoring,
            assignment_service=assignments,
            clock=lambda: now,
        ),
    )
    return create_app(Settings(database_url="sqlite://"), classroom_services=services), factory


def assessment_payload(revision: int) -> dict[str, object]:
    return {
        "experiment_name": "顺序表的基本操作",
        "expected_config_revision": revision,
        "monitoring_scopes": {
            "coding_process": True,
            "revision_process": True,
            "run_and_debug": True,
            "thinking_and_pause": True,
            "paste_behavior": False,
        },
        "evaluation_dimensions": [
            {
                "id": "implementation_quality",
                "name": "实现质量",
                "description": "代码实现与验证质量。",
                "weight_bps": 10000,
                "student_visible": True,
                "order": 1,
            }
        ],
    }


def test_saving_assessment_publishes_binding_and_student_assignments_idempotently() -> None:
    app, factory = make_application()
    headers = {"Authorization": "Bearer teacher-token"}
    scope = "/v1/classroom/experiments/course-1/experiment-1"

    context = request(
        app,
        "PUT",
        f"{scope}/publication-context",
        headers=headers,
        json={
            "experiment_name": "顺序表的基本操作",
            "statement": "完成线性表实验并验证结果。",
            "scheduled_start_at": "2026-09-02T08:00:00Z",
            "scheduled_end_at": "2026-09-09T08:00:00Z",
            "ai_policy": "prohibited",
        },
    )
    assert context.status_code == 200

    assert (
        request(
            app,
            "POST",
            f"{scope}/assessment-config",
            headers=headers,
            json={"experiment_name": "顺序表的基本操作"},
        ).status_code
        == 200
    )
    assert (
        request(
            app,
            "PUT",
            f"{scope}/assessment-config",
            headers=headers,
            json=assessment_payload(0),
        ).status_code
        == 200
    )

    first = request(app, "POST", f"{scope}/assessment-publication", headers=headers)
    retry = request(app, "POST", f"{scope}/assessment-publication", headers=headers)

    assert first.status_code == 200
    assert retry.status_code == 200
    assert retry.json()["plan_version_id"] == first.json()["plan_version_id"]
    assert first.json()["assignment_count"] == 1

    with factory() as session:
        assert session.query(PlanVersion).count() == 1
        binding = session.query(ExperimentPlanBinding).one()
        assignment = session.query(StudentAssignment).one()
        assert binding.plan_version == 1
        assert assignment.binding_id == binding.id
        assert assignment.student_id == "student-1"
        assert assignment.scheduled_end_at.replace(tzinfo=UTC) == datetime(
            2026, 9, 9, 8, 0, tzinfo=UTC
        )


def test_saving_assessment_backfills_context_for_existing_experiments() -> None:
    app, factory = make_application()
    headers = {"Authorization": "Bearer teacher-token"}
    scope = "/v1/classroom/experiments/course-1/experiment-1"

    assert (
        request(
            app,
            "POST",
            f"{scope}/assessment-config",
            headers=headers,
            json={"experiment_name": "历史实验"},
        ).status_code
        == 200
    )
    assert (
        request(
            app,
            "PUT",
            f"{scope}/assessment-config",
            headers=headers,
            json=assessment_payload(0),
        ).status_code
        == 200
    )

    response = request(app, "POST", f"{scope}/assessment-publication", headers=headers)

    assert response.status_code == 200
    with factory() as session:
        assert session.query(ExperimentPlanBinding).count() == 1


def test_changing_assessment_issues_the_next_classroom_version() -> None:
    app, factory = make_application()
    headers = {"Authorization": "Bearer teacher-token"}
    scope = "/v1/classroom/experiments/course-1/experiment-1"
    assert (
        request(
            app,
            "PUT",
            f"{scope}/publication-context",
            headers=headers,
            json={
                "experiment_name": "顺序表的基本操作",
                "statement": "完成线性表实验并验证结果。",
                "scheduled_start_at": "2026-09-02T08:00:00Z",
                "scheduled_end_at": "2026-09-09T08:00:00Z",
                "ai_policy": "prohibited",
            },
        ).status_code
        == 200
    )
    assert (
        request(
            app,
            "POST",
            f"{scope}/assessment-config",
            headers=headers,
            json={"experiment_name": "顺序表的基本操作"},
        ).status_code
        == 200
    )
    assert (
        request(
            app,
            "PUT",
            f"{scope}/assessment-config",
            headers=headers,
            json=assessment_payload(0),
        ).status_code
        == 200
    )
    first = request(app, "POST", f"{scope}/assessment-publication", headers=headers)

    revised = assessment_payload(1)
    revised["evaluation_dimensions"][0]["description"] = "更新后的验证质量要求。"  # type: ignore[index]
    assert (
        request(
            app,
            "PUT",
            f"{scope}/assessment-config",
            headers=headers,
            json=revised,
        ).status_code
        == 200
    )
    second = request(app, "POST", f"{scope}/assessment-publication", headers=headers)

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["plan_id"] == first.json()["plan_id"]
    assert second.json()["version"] == 2
    with factory() as session:
        assert session.query(PlanVersion).count() == 2
        assert session.query(ExperimentPlanBinding).one().plan_version == 2
