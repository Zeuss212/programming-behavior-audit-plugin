"""Durable, owner-scoped classroom plan suggestion jobs."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from classroom_sync.errors import AuthorizationError, UpstreamUnavailableError
from classroom_sync.models import Base, ClassroomPlanSuggestionJob
from classroom_sync.services.plan_suggestion_jobs import PlanSuggestionJobService
from classroom_sync.services.plan_suggestions import (
    PlanSuggestion,
    PlanSuggestionInput,
    SuggestedKnowledgePoint,
)


class RecordingGenerator:
    def __init__(self) -> None:
        self.calls: list[PlanSuggestionInput] = []
        self.error: UpstreamUnavailableError | None = None

    def generate(self, suggestion_input: PlanSuggestionInput) -> PlanSuggestion:
        self.calls.append(suggestion_input)
        if self.error is not None:
            raise self.error
        return PlanSuggestion(
            title="字典课堂练习",
            knowledge_points=(
                SuggestedKnowledgePoint(name="字典读取", description="按键读取并验证结果。"),
            ),
        )


def build_service(now: datetime):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    generator = RecordingGenerator()
    service = PlanSuggestionJobService(
        session_factory,
        generator,
        clock=lambda: now,
        max_attempts=2,
    )
    return service, generator, session_factory


def test_submit_is_idempotent_while_matching_teacher_request_is_active() -> None:
    now = datetime(2026, 8, 19, 10, 0, tzinfo=UTC)
    service, _generator, session_factory = build_service(now)
    source = PlanSuggestionInput(title="", statement="实现字典查询")

    first = service.submit(
        teacher_id="teacher-1",
        space_id="space-1",
        parent_algorithm_id="parent-1",
        suggestion_input=source,
    )
    second = service.submit(
        teacher_id="teacher-1",
        space_id="space-1",
        parent_algorithm_id="parent-1",
        suggestion_input=source,
    )

    assert first == second
    assert first.status == "pending"
    with session_factory() as session:
        jobs = list(session.scalars(select(ClassroomPlanSuggestionJob)))
    assert len(jobs) == 1
    assert jobs[0].suggestion_input == {"title": "", "statement": "实现字典查询"}


def test_worker_persists_only_validated_result_then_erases_teacher_input() -> None:
    now = datetime(2026, 8, 19, 10, 0, tzinfo=UTC)
    service, generator, session_factory = build_service(now)
    submitted = service.submit(
        teacher_id="teacher-1",
        space_id="space-1",
        parent_algorithm_id="parent-1",
        suggestion_input=PlanSuggestionInput(title="", statement="实现字典查询"),
    )

    assert service.run_due_jobs("worker-a") == 1
    ready = service.get_for_teacher(submitted.job_id, teacher_id="teacher-1")

    assert generator.calls == [PlanSuggestionInput(title="", statement="实现字典查询")]
    assert ready.status == "ready"
    assert ready.suggestion is not None
    assert ready.suggestion.title == "字典课堂练习"
    with session_factory() as session:
        job = session.get(ClassroomPlanSuggestionJob, submitted.job_id)
    assert job is not None
    assert job.suggestion_input == {}
    assert job.result == {
        "title": "字典课堂练习",
        "knowledge_points": [{"name": "字典读取", "description": "按键读取并验证结果。"}],
    }


def test_worker_returns_a_safe_failure_after_retry_budget_and_erases_input() -> None:
    now = datetime(2026, 8, 19, 10, 0, tzinfo=UTC)
    service, generator, session_factory = build_service(now)
    generator.error = UpstreamUnavailableError("ai_suggestion_upstream_unavailable")
    submitted = service.submit(
        teacher_id="teacher-1",
        space_id="space-1",
        parent_algorithm_id="parent-1",
        suggestion_input=PlanSuggestionInput(title="", statement="实现字典查询"),
    )

    assert service.run_due_jobs("worker-a") == 1
    assert service.get_for_teacher(submitted.job_id, teacher_id="teacher-1").status == "pending"
    with session_factory() as session:
        retry_at = session.get(ClassroomPlanSuggestionJob, submitted.job_id).run_at  # type: ignore[union-attr]
    assert service.claim_due_jobs("worker-a", retry_at)
    service.record_failure(
        submitted.job_id,
        worker_id="worker-a",
        failure_code="ai_suggestion_upstream_unavailable",
        retry_delay=None,
        occurred_at=retry_at,
    )

    failed = service.get_for_teacher(submitted.job_id, teacher_id="teacher-1")
    assert failed.status == "failed"
    assert failed.failure_code == "ai_suggestion_upstream_unavailable"
    with session_factory() as session:
        job = session.get(ClassroomPlanSuggestionJob, submitted.job_id)
    assert job is not None
    assert job.suggestion_input == {}
    assert job.result is None


def test_job_cannot_be_read_by_a_different_teacher() -> None:
    now = datetime(2026, 8, 19, 10, 0, tzinfo=UTC)
    service, _generator, _session_factory = build_service(now)
    submitted = service.submit(
        teacher_id="teacher-1",
        space_id="space-1",
        parent_algorithm_id="parent-1",
        suggestion_input=PlanSuggestionInput(title="", statement="实现字典查询"),
    )

    with pytest.raises(AuthorizationError, match="plan_suggestion_job_not_owned"):
        service.get_for_teacher(submitted.job_id, teacher_id="teacher-2")
