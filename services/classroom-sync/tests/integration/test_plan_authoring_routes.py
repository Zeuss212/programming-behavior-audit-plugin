"""HTTP boundaries for resumable, teacher-owned plan authoring sessions."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import cast

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from classroom_sync.application import ClassroomServices
from classroom_sync.auth.fincolab import Principal, StudentChildExperiment
from classroom_sync.config import Settings
from classroom_sync.errors import AuthorizationError, UpstreamUnavailableError
from classroom_sync.main import create_app
from classroom_sync.models import Base, ClassroomPlanSuggestionJob
from classroom_sync.services.assignments import AssignmentService
from classroom_sync.services.plan_authoring import PlanAuthoringService
from classroom_sync.services.plan_suggestion_jobs import PlanSuggestionJobService
from classroom_sync.services.plan_suggestions import (
    PlanSuggestion,
    PlanSuggestionInput,
    SuggestedKnowledgePoint,
)
from classroom_sync.services.plans import PlanService

TEACHER_HEADERS = {"Authorization": "Bearer teacher-token"}
OTHER_TEACHER_HEADERS = {"Authorization": "Bearer other-teacher-token"}
PRIVATE_STATEMENT = "根据私有教师材料完成尾插并输出结果。"


class RecordingIdentityGateway:
    def __init__(self) -> None:
        self.deny_parent_ownership = False
        self.resolved_tokens: list[str] = []
        self.owner_checks: list[tuple[str, str, str]] = []

    def resolve_principal(self, bearer_token: str) -> Principal:
        self.resolved_tokens.append(bearer_token)
        if bearer_token == "teacher-token":
            return Principal("teacher-1", "teacher-a", bearer_token)
        if bearer_token == "other-teacher-token":
            return Principal("teacher-2", "teacher-b", bearer_token)
        raise AssertionError(f"Unexpected bearer: {bearer_token}")

    def require_teacher_owner(
        self, principal: Principal, space_id: str, experiment_id: str
    ) -> None:
        self.owner_checks.append((principal.user_id, space_id, experiment_id))
        if self.deny_parent_ownership:
            raise AuthorizationError("teacher_not_experiment_owner")

    def require_student_member(self, principal: Principal, space_id: str) -> None:
        raise AssertionError("Student authorization is not expected")

    def list_student_children(
        self, principal: Principal, space_id: str, parent_algorithm_id: str
    ) -> tuple[StudentChildExperiment, ...]:
        raise AssertionError("Roster lookup is not expected")


class RecordingGenerator:
    retry_provider_errors = False

    def __init__(self) -> None:
        self.calls: list[PlanSuggestionInput] = []
        self.error: UpstreamUnavailableError | None = None

    def generate(self, suggestion_input: PlanSuggestionInput) -> PlanSuggestion:
        self.calls.append(suggestion_input)
        if self.error is not None:
            raise self.error
        return PlanSuggestion(
            title="C++ 链表课堂练习",
            knowledge_points=(
                SuggestedKnowledgePoint(
                    name="尾插",
                    description="将新节点连接到链表末尾。",
                    material_requirement_id="requirement-tail-insert",
                ),
            ),
        )


class AuthoringHarness:
    def __init__(self, *, ai_configured: bool = True) -> None:
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        self.session_factory = sessionmaker(bind=engine, expire_on_commit=False)
        self.identity = RecordingIdentityGateway()
        self.generator = RecordingGenerator()
        self.jobs = PlanSuggestionJobService(
            self.session_factory,
            self.generator,
            clock=lambda: datetime(2026, 8, 28, 9, 0, tzinfo=UTC),
        )
        self.authoring = PlanAuthoringService(
            self.session_factory,
            self.jobs if ai_configured else None,
            clock=lambda: datetime(2026, 8, 28, 9, 0, tzinfo=UTC),
        )
        services = ClassroomServices(
            identity_gateway=self.identity,
            plan_service=cast(PlanService, object()),
            assignment_service=cast(AssignmentService, object()),
            plan_authoring_service=self.authoring,
            plan_suggestion_job_service=self.jobs if ai_configured else None,
        )
        self.app = create_app(Settings(database_url="sqlite://"), classroom_services=services)

    def request(self, method: str, path: str, **kwargs: object) -> httpx.Response:
        async def send() -> httpx.Response:
            transport = httpx.ASGITransport(app=self.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                return await client.request(method, path, **kwargs)

        return asyncio.run(send())

    def create_session(self) -> dict[str, object]:
        response = self.request(
            "POST",
            "/v1/classroom/plan-authoring-sessions",
            headers=TEACHER_HEADERS,
            json={"space_id": "space-1", "parent_algorithm_id": "parent-1"},
        )
        assert response.status_code == 200
        return cast(dict[str, object], response.json())

    def suggestion_path(self, session: dict[str, object]) -> str:
        return (
            "/v1/classroom/plan-authoring-sessions/"
            f"{session['authoring_session_id']}/plan-suggestion"
        )


@pytest.fixture
def harness() -> AuthoringHarness:
    return AuthoringHarness()


def cpp_suggestion_payload(*, statement: str = PRIVATE_STATEMENT) -> dict[str, object]:
    return {
        "profile_kind": "cpp_v3",
        "title": "C++ 链表练习",
        "statement": statement,
        "material_bundle_hash": "a" * 64,
        "material_requirements": [
            {
                "id": "requirement-tail-insert",
                "name": "尾插",
                "source_statement": "教师私有测评维度：在链表末尾插入节点。",
            }
        ],
    }


def assert_safe_suggestion_response(body: dict[str, object]) -> None:
    suggestion = cast(dict[str, object], body["suggestion"])
    assert suggestion["input_hash"] is not None
    serialized = json.dumps(body, ensure_ascii=False)
    assert PRIVATE_STATEMENT not in serialized
    assert "source_statement" not in serialized
    assert "material_requirements" not in serialized
    assert "material_bundle_hash" not in serialized
    assert "provider_metadata" not in serialized.casefold()
    assert "provider_model" not in serialized.casefold()
    assert "raw_response" not in serialized.casefold()
    assert "tests" not in serialized.casefold()


def test_post_create_is_idempotent_for_the_teacher_and_parent_scope(
    harness: AuthoringHarness,
) -> None:
    first = harness.create_session()

    second = harness.create_session()

    assert second == first
    assert first == {
        "authoring_session_id": first["authoring_session_id"],
        "status": "open",
        "space_id": "space-1",
        "parent_algorithm_id": "parent-1",
        "draft_id": None,
        "suggestion": {
            "status": "not_requested",
            "job_id": None,
            "input_hash": None,
        },
    }
    assert harness.identity.owner_checks == [
        ("teacher-1", "space-1", "parent-1"),
        ("teacher-1", "space-1", "parent-1"),
    ]


def test_get_current_recovers_the_existing_session_and_rechecks_parent_ownership(
    harness: AuthoringHarness,
) -> None:
    created = harness.create_session()

    response = harness.request(
        "GET",
        "/v1/classroom/plan-authoring-sessions/current",
        headers=TEACHER_HEADERS,
        params={"space_id": "space-1", "parent_algorithm_id": "parent-1"},
    )

    assert response.status_code == 200
    assert response.json() == created
    assert harness.identity.owner_checks[-1] == (
        "teacher-1",
        "space-1",
        "parent-1",
    )


def test_post_suggestion_links_one_safe_pending_job(harness: AuthoringHarness) -> None:
    session = harness.create_session()

    response = harness.request(
        "POST",
        harness.suggestion_path(session),
        headers=TEACHER_HEADERS,
        json=cpp_suggestion_payload(),
    )

    assert response.status_code == 202
    body = cast(dict[str, object], response.json())
    suggestion = cast(dict[str, object], body["suggestion"])
    assert suggestion["status"] == "pending"
    assert suggestion["job_id"] is not None
    assert_safe_suggestion_response(body)
    assert harness.identity.owner_checks[-1] == (
        "teacher-1",
        "space-1",
        "parent-1",
    )


def test_get_suggestion_refreshes_a_ready_result_without_private_input(
    harness: AuthoringHarness,
) -> None:
    session = harness.create_session()
    pending = harness.request(
        "POST",
        harness.suggestion_path(session),
        headers=TEACHER_HEADERS,
        json=cpp_suggestion_payload(),
    ).json()
    assert harness.jobs.run_due_jobs("worker-1") == 1

    response = harness.request("GET", harness.suggestion_path(session), headers=TEACHER_HEADERS)

    assert response.status_code == 200
    body = cast(dict[str, object], response.json())
    suggestion = cast(dict[str, object], body["suggestion"])
    assert suggestion == {
        "status": "ready",
        "job_id": pending["suggestion"]["job_id"],
        "input_hash": pending["suggestion"]["input_hash"],
        "suggestion": {
            "title": "C++ 链表课堂练习",
            "knowledge_points": [
                {
                    "name": "尾插",
                    "description": "将新节点连接到链表末尾。",
                    "material_requirement_id": "requirement-tail-insert",
                }
            ],
        },
    }
    assert_safe_suggestion_response(body)


def test_duplicate_post_with_changed_input_returns_the_original_attempt(
    harness: AuthoringHarness,
) -> None:
    session = harness.create_session()
    first = harness.request(
        "POST",
        harness.suggestion_path(session),
        headers=TEACHER_HEADERS,
        json=cpp_suggestion_payload(),
    )

    duplicate = harness.request(
        "POST",
        harness.suggestion_path(session),
        headers=TEACHER_HEADERS,
        json=cpp_suggestion_payload(statement="等待期间已修改的第二份题目。"),
    )

    assert duplicate.status_code == 202
    assert duplicate.json() == first.json()
    with harness.session_factory() as database_session:
        jobs = list(database_session.scalars(select(ClassroomPlanSuggestionJob)))
    assert len(jobs) == 1


def test_terminal_failure_is_refreshable_and_duplicate_post_does_not_regenerate(
    harness: AuthoringHarness,
) -> None:
    harness.generator.error = UpstreamUnavailableError(
        "ai_provider_request_rejected", retryable=False
    )
    session = harness.create_session()
    pending = harness.request(
        "POST",
        harness.suggestion_path(session),
        headers=TEACHER_HEADERS,
        json=cpp_suggestion_payload(),
    ).json()
    assert harness.jobs.run_due_jobs("worker-1") == 1

    failed = harness.request("GET", harness.suggestion_path(session), headers=TEACHER_HEADERS)
    duplicate = harness.request(
        "POST",
        harness.suggestion_path(session),
        headers=TEACHER_HEADERS,
        json=cpp_suggestion_payload(statement="不得触发的第二份题目。"),
    )

    assert failed.status_code == 200
    assert duplicate.status_code == 202
    assert failed.json() == duplicate.json()
    suggestion = failed.json()["suggestion"]
    assert suggestion == {
        "status": "failed",
        "job_id": pending["suggestion"]["job_id"],
        "input_hash": pending["suggestion"]["input_hash"],
        "failure_code": "ai_provider_request_rejected",
    }
    assert len(harness.generator.calls) == 1
    assert harness.jobs.run_due_jobs("worker-2") == 0
    assert_safe_suggestion_response(cast(dict[str, object], failed.json()))


def test_post_abandon_closes_the_owned_session_and_releases_a_pending_attempt(
    harness: AuthoringHarness,
) -> None:
    session = harness.create_session()
    harness.request(
        "POST",
        harness.suggestion_path(session),
        headers=TEACHER_HEADERS,
        json=cpp_suggestion_payload(),
    )

    response = harness.request(
        "POST",
        f"/v1/classroom/plan-authoring-sessions/{session['authoring_session_id']}/abandon",
        headers=TEACHER_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "abandoned"
    assert body["suggestion"]["status"] == "failed"
    assert body["suggestion"]["failure_code"] == "ai_suggestion_authoring_abandoned"
    assert_safe_suggestion_response(cast(dict[str, object], body))


def test_no_ai_configuration_keeps_manual_authoring_routes_available() -> None:
    harness = AuthoringHarness(ai_configured=False)
    session = harness.create_session()

    current = harness.request(
        "GET",
        "/v1/classroom/plan-authoring-sessions/current",
        headers=TEACHER_HEADERS,
        params={"space_id": "space-1", "parent_algorithm_id": "parent-1"},
    )
    suggestion_status = harness.request(
        "GET", harness.suggestion_path(session), headers=TEACHER_HEADERS
    )
    abandoned = harness.request(
        "POST",
        f"/v1/classroom/plan-authoring-sessions/{session['authoring_session_id']}/abandon",
        headers=TEACHER_HEADERS,
    )

    assert current.status_code == 200
    assert suggestion_status.status_code == 200
    assert suggestion_status.json()["suggestion"] == {
        "status": "not_requested",
        "job_id": None,
        "input_hash": None,
    }
    assert abandoned.status_code == 200
    assert abandoned.json()["status"] == "abandoned"


def test_no_ai_configuration_rejects_only_suggestion_creation_without_mutation() -> None:
    harness = AuthoringHarness(ai_configured=False)
    session = harness.create_session()

    response = harness.request(
        "POST",
        harness.suggestion_path(session),
        headers=TEACHER_HEADERS,
        json=cpp_suggestion_payload(),
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "ai_suggestion_not_configured"
    recovered = harness.authoring.get_current(
        teacher_id="teacher-1", space_id="space-1", parent_algorithm_id="parent-1"
    )
    assert recovered.status == "open"
    assert recovered.suggestion.status == "not_requested"


def test_abandon_still_cancels_a_pending_attempt_after_ai_is_disabled() -> None:
    harness = AuthoringHarness()
    session = harness.create_session()
    harness.request(
        "POST",
        harness.suggestion_path(session),
        headers=TEACHER_HEADERS,
        json=cpp_suggestion_payload(),
    )
    object.__setattr__(
        harness.app.state.classroom_services,
        "plan_authoring_service",
        PlanAuthoringService(
            harness.session_factory,
            None,
            clock=lambda: datetime(2026, 8, 28, 9, 0, tzinfo=UTC),
        ),
    )

    response = harness.request(
        "POST",
        f"/v1/classroom/plan-authoring-sessions/{session['authoring_session_id']}/abandon",
        headers=TEACHER_HEADERS,
    )

    assert response.status_code == 200
    assert response.json()["status"] == "abandoned"
    assert response.json()["suggestion"]["status"] == "failed"
    assert response.json()["suggestion"]["failure_code"] == "ai_suggestion_authoring_abandoned"


@pytest.mark.parametrize(
    "endpoint", ("create", "current", "suggestion-post", "suggestion-get", "abandon")
)
def test_every_authoring_request_rejects_a_missing_bearer(
    harness: AuthoringHarness, endpoint: str
) -> None:
    session = harness.create_session()
    routes: dict[str, tuple[str, str, dict[str, object]]] = {
        "create": (
            "POST",
            "/v1/classroom/plan-authoring-sessions",
            {"json": {"space_id": "space-1", "parent_algorithm_id": "parent-1"}},
        ),
        "current": (
            "GET",
            "/v1/classroom/plan-authoring-sessions/current",
            {"params": {"space_id": "space-1", "parent_algorithm_id": "parent-1"}},
        ),
        "suggestion-post": (
            "POST",
            harness.suggestion_path(session),
            {"json": cpp_suggestion_payload()},
        ),
        "suggestion-get": ("GET", harness.suggestion_path(session), {}),
        "abandon": (
            "POST",
            f"/v1/classroom/plan-authoring-sessions/{session['authoring_session_id']}/abandon",
            {},
        ),
    }
    method, path, kwargs = routes[endpoint]

    response = harness.request(method, path, **kwargs)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "missing_bearer_token"


@pytest.mark.parametrize("endpoint", ("suggestion-post", "suggestion-get", "abandon"))
def test_a_different_teacher_cannot_use_a_known_authoring_session_uuid(
    harness: AuthoringHarness, endpoint: str
) -> None:
    session = harness.create_session()
    routes: dict[str, tuple[str, str, dict[str, object]]] = {
        "suggestion-post": (
            "POST",
            harness.suggestion_path(session),
            {"json": cpp_suggestion_payload()},
        ),
        "suggestion-get": ("GET", harness.suggestion_path(session), {}),
        "abandon": (
            "POST",
            f"/v1/classroom/plan-authoring-sessions/{session['authoring_session_id']}/abandon",
            {},
        ),
    }
    method, path, kwargs = routes[endpoint]

    response = harness.request(method, path, headers=OTHER_TEACHER_HEADERS, **kwargs)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "plan_authoring_session_not_owned"
    assert (
        harness.authoring.get_current(
            teacher_id="teacher-1", space_id="space-1", parent_algorithm_id="parent-1"
        ).status
        == "open"
    )


@pytest.mark.parametrize(
    "endpoint", ("create", "current", "suggestion-post", "suggestion-get", "abandon")
)
def test_every_authoring_request_rechecks_current_parent_ownership_before_use(
    harness: AuthoringHarness, endpoint: str
) -> None:
    session = harness.create_session()
    harness.identity.deny_parent_ownership = True
    routes: dict[str, tuple[str, str, dict[str, object]]] = {
        "create": (
            "POST",
            "/v1/classroom/plan-authoring-sessions",
            {"json": {"space_id": "space-1", "parent_algorithm_id": "parent-1"}},
        ),
        "current": (
            "GET",
            "/v1/classroom/plan-authoring-sessions/current",
            {"params": {"space_id": "space-1", "parent_algorithm_id": "parent-1"}},
        ),
        "suggestion-post": (
            "POST",
            harness.suggestion_path(session),
            {"json": cpp_suggestion_payload()},
        ),
        "suggestion-get": ("GET", harness.suggestion_path(session), {}),
        "abandon": (
            "POST",
            f"/v1/classroom/plan-authoring-sessions/{session['authoring_session_id']}/abandon",
            {},
        ),
    }
    method, path, kwargs = routes[endpoint]

    response = harness.request(method, path, headers=TEACHER_HEADERS, **kwargs)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "teacher_not_experiment_owner"
    harness.identity.deny_parent_ownership = False
    recovered = harness.authoring.get_current(
        teacher_id="teacher-1", space_id="space-1", parent_algorithm_id="parent-1"
    )
    assert recovered.status == "open"
    assert recovered.suggestion.status == "not_requested"
