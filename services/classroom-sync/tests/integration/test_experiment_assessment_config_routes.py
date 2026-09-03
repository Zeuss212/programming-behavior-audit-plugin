"""Experiment-scoped assessment configuration HTTP contract."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import cast

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from classroom_sync.application import ClassroomServices
from classroom_sync.auth.fincolab import Principal, StudentChildExperiment
from classroom_sync.config import Settings
from classroom_sync.main import create_app
from classroom_sync.models import Base
from classroom_sync.services.assignments import AssignmentService
from classroom_sync.services.experiment_assessment_configs import (
    ExperimentAssessmentConfigService,
)
from classroom_sync.services.plans import PlanService


class IdentityGateway:
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
        raise AssertionError("roster lookup is not expected")


def request(app: object, method: str, path: str, **kwargs: object) -> httpx.Response:
    async def send() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(send())


def make_application() -> object:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    services = ClassroomServices(
        identity_gateway=IdentityGateway(),
        plan_service=cast(PlanService, object()),
        assignment_service=cast(AssignmentService, object()),
        experiment_assessment_config_service=ExperimentAssessmentConfigService(
            factory,
            clock=lambda: datetime(2026, 9, 2, 8, 0, tzinfo=UTC),
        ),
    )
    return create_app(Settings(database_url="sqlite://"), classroom_services=services)


def default_dimensions() -> list[dict[str, object]]:
    return [
        {
            "id": "implementation_quality",
            "name": "实现质量",
            "description": "代码实现与验证质量。",
            "weight_bps": 10000,
            "student_visible": True,
            "order": 1,
        }
    ]


def test_existing_experiment_can_create_and_read_assessment_without_plan() -> None:
    app = make_application()
    path = "/v1/classroom/experiments/course-1/experiment-1/assessment-config"
    headers = {"Authorization": "Bearer teacher-token"}

    created = request(
        app,
        "POST",
        path,
        headers=headers,
        json={"experiment_name": "顺序表的基本操作"},
    )
    loaded = request(app, "GET", path, headers=headers)

    assert created.status_code == 200
    assert loaded.status_code == 200
    assert loaded.json()["experiment_name"] == "顺序表的基本操作"
    assert loaded.json()["config_revision"] == 0
    assert loaded.json()["total_bps"] == 10000
    assert len(loaded.json()["evaluation_dimensions"]) == 5


def test_teacher_updates_independent_assessment_with_optimistic_locking() -> None:
    app = make_application()
    path = "/v1/classroom/experiments/course-1/experiment-1/assessment-config"
    headers = {"Authorization": "Bearer teacher-token"}
    request(
        app,
        "POST",
        path,
        headers=headers,
        json={"experiment_name": "顺序表的基本操作"},
    )
    payload = {
        "experiment_name": "顺序表的基本操作",
        "expected_config_revision": 0,
        "monitoring_scopes": {
            "coding_process": True,
            "revision_process": True,
            "run_and_debug": True,
            "thinking_and_pause": True,
            "paste_behavior": False,
        },
        "evaluation_dimensions": default_dimensions(),
    }

    updated = request(app, "PUT", path, headers=headers, json=payload)
    stale = request(app, "PUT", path, headers=headers, json=payload)

    assert updated.status_code == 200
    assert updated.json()["config_revision"] == 1
    assert updated.json()["monitoring_scopes"]["paste_behavior"] is False
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "assessment_config_stale"


def test_experiment_name_and_weights_are_validated() -> None:
    app = make_application()
    path = "/v1/classroom/experiments/course-1/experiment-1/assessment-config"
    headers = {"Authorization": "Bearer teacher-token"}

    invalid_name = request(
        app,
        "POST",
        path,
        headers=headers,
        json={"experiment_name": "   "},
    )

    assert invalid_name.status_code == 422
