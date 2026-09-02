"""Teacher-owned persistence and optimistic locking for assessment configs."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

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
from classroom_sync.models import Base, PlanDraft
from classroom_sync.services.assessment_configs import AssessmentConfigService
from classroom_sync.services.assignments import AssignmentService
from classroom_sync.services.plan_authoring import PlanAuthoringService
from classroom_sync.services.plans import PlanDraftInput, PlanService
from classroom_sync.services.read_models import ClassroomReadService
from tests.integration.test_plan_assignment_flow import profile_draft

TEACHER_HEADERS = {"Authorization": "Bearer teacher-token"}
OTHER_TEACHER_HEADERS = {"Authorization": "Bearer other-teacher-token"}


class RecordingIdentityGateway:
    def __init__(self) -> None:
        self.owner_checks: list[tuple[str, str, str]] = []

    def resolve_principal(self, bearer_token: str) -> Principal:
        if bearer_token == "teacher-token":
            return Principal("teacher-1", "teacher-a", bearer_token)
        if bearer_token == "other-teacher-token":
            return Principal("teacher-2", "teacher-b", bearer_token)
        raise AssertionError(f"unexpected bearer: {bearer_token}")

    def require_teacher_owner(
        self, principal: Principal, space_id: str, experiment_id: str
    ) -> None:
        self.owner_checks.append((principal.user_id, space_id, experiment_id))
        if principal.user_id != "teacher-1":
            raise AuthorizationError("teacher_not_experiment_owner")

    def require_student_member(self, principal: Principal, space_id: str) -> None:
        raise AssertionError("student authorization is not expected")

    def list_student_children(
        self, principal: Principal, space_id: str, parent_algorithm_id: str
    ) -> tuple[StudentChildExperiment, ...]:
        raise AssertionError("roster lookup is not expected")


class AssessmentHarness:
    def __init__(self) -> None:
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        self.session_factory = sessionmaker(bind=engine, expire_on_commit=False)
        root = Path(__file__).resolve().parents[4]
        schemas = ClassroomSchemaRegistry(root / "contracts" / "classroom" / "v1")
        self.now = datetime(2026, 9, 2, 4, 0, tzinfo=UTC)
        self.plans = PlanService(self.session_factory, schemas, clock=lambda: self.now)
        self.configs = AssessmentConfigService(
            self.session_factory,
            clock=lambda: self.now,
        )
        self.identity = RecordingIdentityGateway()
        self.assignments = AssignmentService(self.session_factory, clock=lambda: self.now)
        self.authoring = PlanAuthoringService(
            self.session_factory,
            None,
            clock=lambda: self.now,
        )
        self.draft = self.plans.create_draft(
            PlanDraftInput(
                space_id="space-1",
                parent_algorithm_id="parent-1",
                title="字典课堂练习",
                profile=profile_draft("学生是否正确读取字典中的值？"),
                scheduled_start_at=self.now,
                scheduled_end_at=self.now + timedelta(hours=1),
                ai_policy="prohibited",
            ),
            teacher_id="teacher-1",
        )
        services = ClassroomServices(
            identity_gateway=self.identity,
            plan_service=self.plans,
            assignment_service=self.assignments,
            plan_authoring_service=self.authoring,
            read_service=ClassroomReadService(self.session_factory),
            assessment_config_service=self.configs,
        )
        self.app = create_app(Settings(database_url="sqlite://"), classroom_services=services)

    def request(self, method: str, path: str, **kwargs: object) -> httpx.Response:
        async def send() -> httpx.Response:
            transport = httpx.ASGITransport(app=self.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                return await client.request(method, path, **kwargs)

        return asyncio.run(send())

    @property
    def path(self) -> str:
        return f"/v1/classroom/plans/drafts/{self.draft.id}/assessment-config"


@pytest.fixture
def harness() -> AssessmentHarness:
    return AssessmentHarness()


def valid_config_payload(*, draft_revision: int, config_revision: int) -> dict[str, object]:
    return {
        "expected_draft_revision": draft_revision,
        "expected_config_revision": config_revision,
        "monitoring_scopes": {
            "coding_process": True,
            "revision_process": True,
            "run_and_debug": True,
            "thinking_and_pause": True,
            "paste_behavior": False,
        },
        "evaluation_dimensions": [
            {
                "id": "knowledge_mastery",
                "name": "知识点掌握",
                "description": "理解并应用本实验知识点。",
                "weight_bps": 6000,
                "student_visible": True,
                "order": 1,
            },
            {
                "id": "debugging",
                "name": "调试能力",
                "description": "识别、定位并修正错误。",
                "weight_bps": 4000,
                "student_visible": False,
                "order": 2,
            },
        ],
    }


def test_get_materializes_one_stable_default_config(harness: AssessmentHarness) -> None:
    first = harness.request("GET", harness.path, headers=TEACHER_HEADERS)
    second = harness.request("GET", harness.path, headers=TEACHER_HEADERS)

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json() == first.json()
    body = first.json()
    assert body["draft_id"] == harness.draft.id
    assert body["draft_revision"] == 0
    assert body["config_revision"] == 0
    assert body["schema_version"] == 1
    assert body["monitoring_scopes"] == {
        "coding_process": True,
        "revision_process": True,
        "run_and_debug": True,
        "thinking_and_pause": True,
        "paste_behavior": True,
    }
    assert [row["id"] for row in body["evaluation_dimensions"]] == [
        "knowledge_mastery",
        "debugging_ability",
        "problem_solving",
        "learning_process",
        "coding_habits",
    ]
    assert [row["weight_bps"] for row in body["evaluation_dimensions"]] == [
        3000,
        2500,
        2000,
        1500,
        1000,
    ]
    assert body["total_bps"] == 10000
    assert harness.identity.owner_checks == [
        ("teacher-1", "space-1", "parent-1"),
        ("teacher-1", "space-1", "parent-1"),
    ]


def test_put_persists_config_and_bumps_both_revisions(harness: AssessmentHarness) -> None:
    initial = harness.request("GET", harness.path, headers=TEACHER_HEADERS).json()
    payload = valid_config_payload(
        draft_revision=initial["draft_revision"],
        config_revision=initial["config_revision"],
    )

    saved = harness.request("PUT", harness.path, headers=TEACHER_HEADERS, json=payload)
    refreshed = harness.request("GET", harness.path, headers=TEACHER_HEADERS)

    assert saved.status_code == 200
    assert refreshed.status_code == 200
    assert refreshed.json() == saved.json()
    body = saved.json()
    assert body["draft_revision"] == 1
    assert body["config_revision"] == 1
    assert body["evaluation_dimensions"] == payload["evaluation_dimensions"]
    assert body["total_bps"] == 10000
    with harness.session_factory() as session:
        draft = session.get(PlanDraft, harness.draft.id)
        assert draft is not None
        assert draft.revision == 1


def test_put_rejects_stale_draft_or_config_without_overwriting(
    harness: AssessmentHarness,
) -> None:
    initial = harness.request("GET", harness.path, headers=TEACHER_HEADERS).json()
    first_payload = valid_config_payload(
        draft_revision=initial["draft_revision"],
        config_revision=initial["config_revision"],
    )
    assert harness.request(
        "PUT", harness.path, headers=TEACHER_HEADERS, json=first_payload
    ).status_code == 200

    stale = harness.request(
        "PUT",
        harness.path,
        headers=TEACHER_HEADERS,
        json=first_payload,
    )

    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "assessment_config_stale"
    assert stale.json()["error"]["retryable"] is False
    assert harness.request("GET", harness.path, headers=TEACHER_HEADERS).json()[
        "evaluation_dimensions"
    ] == first_payload["evaluation_dimensions"]


def test_config_routes_require_both_draft_owner_and_current_parent_owner(
    harness: AssessmentHarness,
) -> None:
    response = harness.request("GET", harness.path, headers=OTHER_TEACHER_HEADERS)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "plan_draft_owner_mismatch"
    assert harness.identity.owner_checks == []


@pytest.mark.parametrize(
    ("mutate", "expected_code"),
    [
        (
            lambda payload: cast(list[dict[str, object]], payload["evaluation_dimensions"])[
                0
            ].update(weight_bps=5999),
            "assessment_weight_total_invalid",
        ),
        (
            lambda payload: cast(list[dict[str, object]], payload["evaluation_dimensions"])[
                1
            ].update(id="knowledge_mastery"),
            "assessment_dimension_duplicate",
        ),
        (
            lambda payload: cast(list[dict[str, object]], payload["evaluation_dimensions"])[
                1
            ].update(order=1),
            "assessment_dimension_duplicate",
        ),
        (
            lambda payload: cast(dict[str, object], payload["monitoring_scopes"]).update(
                unknown_scope=True
            ),
            "assessment_config_invalid",
        ),
        (
            lambda payload: cast(list[dict[str, object]], payload["evaluation_dimensions"])[
                0
            ].update(name=""),
            "assessment_config_invalid",
        ),
    ],
)
def test_put_returns_specific_validation_codes(
    harness: AssessmentHarness,
    mutate: Callable[[dict[str, object]], None],
    expected_code: str,
) -> None:
    initial = harness.request("GET", harness.path, headers=TEACHER_HEADERS).json()
    payload = valid_config_payload(
        draft_revision=initial["draft_revision"],
        config_revision=initial["config_revision"],
    )
    mutate(payload)

    response = harness.request("PUT", harness.path, headers=TEACHER_HEADERS, json=payload)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == expected_code
    assert harness.request("GET", harness.path, headers=TEACHER_HEADERS).json()[
        "config_revision"
    ] == 0


def test_post_assessment_draft_recovers_the_bound_version_and_is_idempotent(
    harness: AssessmentHarness,
) -> None:
    source = harness.plans.create_draft(
        PlanDraftInput(
            space_id="space-1",
            parent_algorithm_id="parent-recovery",
            title="可恢复课堂",
            profile=profile_draft("恢复后的评价问题"),
            scheduled_start_at=harness.now + timedelta(hours=2),
            scheduled_end_at=harness.now + timedelta(hours=3),
            ai_policy="allowed",
        ),
        teacher_id="teacher-1",
    )
    published = harness.plans.publish_draft(source.id, teacher_id="teacher-1")
    harness.assignments.sync_assignments(published, ())
    path = "/v1/classroom/experiments/space-1/parent-recovery/assessment-drafts"

    first = harness.request("POST", path, headers=TEACHER_HEADERS)
    second = harness.request("POST", path, headers=TEACHER_HEADERS)

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json() == first.json()
    body = first.json()
    assert body["draft_id"] != source.id
    assert body["space_id"] == "space-1"
    assert body["parent_algorithm_id"] == "parent-recovery"
    assert body["title"] == "字典数据结构"
    assert body["profile"]["title"] == "字典数据结构"
    assert body["scheduled_start_at"] == "2026-09-02T06:00:00Z"
    assert body["scheduled_end_at"] == "2026-09-02T07:00:00Z"
    assert body["ai_policy"] == "allowed"
    assert body["revision"] == 0


def test_post_assessment_draft_refuses_to_invent_a_missing_plan(
    harness: AssessmentHarness,
) -> None:
    response = harness.request(
        "POST",
        "/v1/classroom/experiments/space-1/never-published/assessment-drafts",
        headers=TEACHER_HEADERS,
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "experiment_plan_binding_not_found"
