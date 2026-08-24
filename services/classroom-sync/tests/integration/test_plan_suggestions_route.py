"""HTTP boundaries for transient, teacher-authorized AI plan suggestions."""

from __future__ import annotations

import asyncio
from typing import cast

import httpx

from classroom_sync.application import ClassroomServices
from classroom_sync.auth.fincolab import Principal, StudentChildExperiment
from classroom_sync.config import Settings
from classroom_sync.errors import AuthorizationError
from classroom_sync.main import create_app
from classroom_sync.services.assignments import AssignmentService
from classroom_sync.services.plan_suggestion_jobs import PlanSuggestionJobSnapshot
from classroom_sync.services.plan_suggestions import (
    AutomaticEvaluation,
    AutomaticEvaluationRequirement,
    PlanSuggestion,
    PlanSuggestionInput,
    SuggestedKnowledgePoint,
)
from classroom_sync.services.plans import PlanService


class FakeIdentityGateway:
    def __init__(self, *, owns_experiment: bool = True) -> None:
        self.owns_experiment = owns_experiment
        self.teacher_checks: list[tuple[str, str, str]] = []

    def resolve_principal(self, bearer_token: str) -> Principal:
        assert bearer_token == "teacher-token"
        return Principal("teacher-1", "teacher-a", bearer_token)

    def require_teacher_owner(
        self, principal: Principal, space_id: str, experiment_id: str
    ) -> None:
        self.teacher_checks.append((principal.user_id, space_id, experiment_id))
        if not self.owns_experiment:
            raise AuthorizationError("teacher_not_experiment_owner")

    def require_student_member(self, principal: Principal, space_id: str) -> None:
        raise AssertionError("Student check is not expected")

    def list_student_children(
        self, principal: Principal, space_id: str, parent_algorithm_id: str
    ) -> tuple[StudentChildExperiment, ...]:
        raise AssertionError("Roster lookup is not expected")


class RecordingSuggestionJobService:
    def __init__(self) -> None:
        self.submit_calls: list[tuple[str, str, str, PlanSuggestionInput]] = []
        self.read_calls: list[tuple[str, str]] = []
        self.snapshot = PlanSuggestionJobSnapshot(
            job_id="suggestion-job-1",
            status="pending",
            failure_code=None,
            suggestion=None,
        )

    def submit(
        self,
        *,
        teacher_id: str,
        space_id: str,
        parent_algorithm_id: str,
        suggestion_input: PlanSuggestionInput,
    ) -> PlanSuggestionJobSnapshot:
        self.submit_calls.append(
            (teacher_id, space_id, parent_algorithm_id, suggestion_input)
        )
        return self.snapshot

    def get_for_teacher(self, job_id: str, *, teacher_id: str) -> PlanSuggestionJobSnapshot:
        self.read_calls.append((job_id, teacher_id))
        return self.snapshot

    @staticmethod
    def ready_snapshot() -> PlanSuggestionJobSnapshot:
        return PlanSuggestionJobSnapshot(
            job_id="suggestion-job-1",
            status="ready",
            failure_code=None,
            suggestion=PlanSuggestion(
                title="字典课堂练习",
                knowledge_points=(
                    SuggestedKnowledgePoint(
                        name="字典读取",
                        description="按键读取并验证结果。",
                        automatic_evaluation=AutomaticEvaluation(
                            mode="all",
                            summary="创建字典并成功运行后可以自动确认。",
                            requirements=[
                                AutomaticEvaluationRequirement(
                                    kind="successful_execution"
                                ),
                                AutomaticEvaluationRequirement(
                                    kind="dict_literal_assignment"
                                ),
                            ],
                        ),
                    ),
                ),
            ),
        )


def request(app, method: str, path: str, **kwargs: object) -> httpx.Response:
    async def send() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(send())


def create_suggestion_app(
    identity_gateway: FakeIdentityGateway,
    suggestion_job_service: RecordingSuggestionJobService | None,
):
    return create_app(
        Settings(database_url="sqlite://"),
        classroom_services=ClassroomServices(
            identity_gateway=identity_gateway,
            plan_service=cast(PlanService, object()),
            assignment_service=cast(AssignmentService, object()),
            plan_suggestion_job_service=suggestion_job_service,
        ),
    )


def suggestion_payload() -> dict[str, str]:
    return {
        "space_id": "space-1",
        "parent_algorithm_id": "parent-1",
        "title": "",
        "statement": "实现字典查询",
    }


def test_teacher_owner_starts_a_durable_plan_suggestion_job() -> None:
    identity_gateway = FakeIdentityGateway()
    suggestion_job_service = RecordingSuggestionJobService()
    response = request(
        create_suggestion_app(identity_gateway, suggestion_job_service),
        "POST",
        "/v1/classroom/plan-suggestions",
        headers={"Authorization": "Bearer teacher-token"},
        json=suggestion_payload(),
    )

    assert response.status_code == 202
    assert response.json() == {
        "job_id": "suggestion-job-1",
        "status": "pending",
    }
    assert identity_gateway.teacher_checks == [("teacher-1", "space-1", "parent-1")]
    assert suggestion_job_service.submit_calls == [
        (
            "teacher-1",
            "space-1",
            "parent-1",
            PlanSuggestionInput(title="", statement="实现字典查询"),
        )
    ]


def test_teacher_owner_can_poll_only_its_safe_ready_suggestion() -> None:
    identity_gateway = FakeIdentityGateway()
    suggestion_job_service = RecordingSuggestionJobService()
    suggestion_job_service.snapshot = suggestion_job_service.ready_snapshot()

    response = request(
        create_suggestion_app(identity_gateway, suggestion_job_service),
        "GET",
        "/v1/classroom/plan-suggestions/suggestion-job-1",
        headers={"Authorization": "Bearer teacher-token"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "job_id": "suggestion-job-1",
        "status": "ready",
        "suggestion": {
            "title": "字典课堂练习",
            "knowledge_points": [
                {
                    "name": "字典读取",
                    "description": "按键读取并验证结果。",
                    "automatic_evaluation": {
                        "mode": "all",
                        "summary": "创建字典并成功运行后可以自动确认。",
                        "requirements": [
                            {"kind": "successful_execution"},
                            {"kind": "dict_literal_assignment"},
                        ],
                    },
                }
            ],
        },
    }
    assert suggestion_job_service.read_calls == [("suggestion-job-1", "teacher-1")]


def test_missing_bearer_is_rejected_before_ai_service_is_called() -> None:
    suggestion_job_service = RecordingSuggestionJobService()
    response = request(
        create_suggestion_app(FakeIdentityGateway(), suggestion_job_service),
        "POST",
        "/v1/classroom/plan-suggestions",
        json=suggestion_payload(),
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "missing_bearer_token"
    assert suggestion_job_service.submit_calls == []


def test_unowned_experiment_is_rejected_before_ai_service_is_called() -> None:
    suggestion_job_service = RecordingSuggestionJobService()
    response = request(
        create_suggestion_app(
            FakeIdentityGateway(owns_experiment=False), suggestion_job_service
        ),
        "POST",
        "/v1/classroom/plan-suggestions",
        headers={"Authorization": "Bearer teacher-token"},
        json=suggestion_payload(),
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "teacher_not_experiment_owner"
    assert suggestion_job_service.submit_calls == []


def test_unconfigured_ai_service_returns_a_stable_error_without_persistence() -> None:
    response = request(
        create_suggestion_app(FakeIdentityGateway(), None),
        "POST",
        "/v1/classroom/plan-suggestions",
        headers={"Authorization": "Bearer teacher-token"},
        json=suggestion_payload(),
    )

    assert response.status_code == 503
    assert response.json()["error"] == {
        "code": "ai_suggestion_not_configured",
        "message": "课堂服务请求未能完成。",
        "retryable": False,
        "request_id": response.json()["error"]["request_id"],
    }


def test_request_rejects_empty_statement_and_extra_fields() -> None:
    payload = {**suggestion_payload(), "statement": "", "unexpected": "nope"}
    response = request(
        create_suggestion_app(FakeIdentityGateway(), RecordingSuggestionJobService()),
        "POST",
        "/v1/classroom/plan-suggestions",
        headers={"Authorization": "Bearer teacher-token"},
        json=payload,
    )

    assert response.status_code == 422
