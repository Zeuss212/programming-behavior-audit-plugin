"""Durable, owner-scoped classroom plan suggestion jobs."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
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
    def __init__(self, *, retry_provider_errors: bool = False) -> None:
        self.calls: list[PlanSuggestionInput] = []
        self.error: UpstreamUnavailableError | None = None
        self.retry_provider_errors = retry_provider_errors

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


def build_service(now: datetime, *, retry_provider_errors: bool = False):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    generator = RecordingGenerator(retry_provider_errors=retry_provider_errors)
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
    assert jobs[0].suggestion_input == {
        "profile_kind": "python_v2",
        "title": "",
        "statement": "实现字典查询",
        "material_bundle_hash": None,
        "material_requirements": [],
    }


def test_submit_binds_the_job_to_its_authoring_session_and_exposes_only_the_input_hash() -> None:
    now = datetime(2026, 8, 19, 10, 0, tzinfo=UTC)
    service, _generator, session_factory = build_service(now)

    submitted = service.submit(
        authoring_session_id="authoring-1",
        teacher_id="teacher-1",
        space_id="space-1",
        parent_algorithm_id="parent-1",
        suggestion_input=PlanSuggestionInput(title="", statement="实现字典查询"),
    )

    assert (
        submitted.input_hash
        == "c97b3366eadd18ad59c168bb99b2a2032f83315d84d65487301e5b8bd6af5941"
    )
    assert not hasattr(submitted, "suggestion_input")
    with session_factory() as session:
        job = session.get(ClassroomPlanSuggestionJob, submitted.job_id)
    assert job is not None
    assert job.authoring_session_id == "authoring-1"


def test_cpp_input_hash_excludes_requirement_text_and_other_private_materials() -> None:
    now = datetime(2026, 8, 19, 10, 0, tzinfo=UTC)
    service, _generator, _session_factory = build_service(now)
    common = {
        "profile_kind": "cpp_v3",
        "title": "C++ 练习",
        "statement": "计算累加值。",
        "material_bundle_hash": "b" * 64,
    }

    first = service.submit(
        teacher_id="teacher-1",
        space_id="space-1",
        parent_algorithm_id="parent-1",
        suggestion_input=PlanSuggestionInput(
            **common,
            material_requirements=(
                {"id": "r1", "name": "累加", "source_statement": "私有材料原文 A"},
            ),
        ),
    )
    second = service.submit(
        teacher_id="teacher-2",
        space_id="space-1",
        parent_algorithm_id="parent-1",
        suggestion_input=PlanSuggestionInput(
            **common,
            material_requirements=(
                {"id": "r2", "name": "完全不同", "source_statement": "私有材料原文 B"},
            ),
        ),
    )

    assert first.input_hash == second.input_hash


def test_an_authoring_session_job_cannot_be_reused_by_a_different_teacher() -> None:
    now = datetime(2026, 8, 19, 10, 0, tzinfo=UTC)
    service, _generator, _session_factory = build_service(now)
    service.submit(
        authoring_session_id="authoring-1",
        teacher_id="teacher-1",
        space_id="space-1",
        parent_algorithm_id="parent-1",
        suggestion_input=PlanSuggestionInput(title="", statement="实现字典查询"),
    )

    with pytest.raises(AuthorizationError, match="plan_suggestion_job_not_owned"):
        service.submit(
            authoring_session_id="authoring-1",
            teacher_id="teacher-2",
            space_id="space-1",
            parent_algorithm_id="parent-1",
            suggestion_input=PlanSuggestionInput(title="", statement="窃取原始任务"),
        )


def test_submit_returns_concurrent_winner_after_unique_insert_race() -> None:
    now = datetime(2026, 8, 19, 10, 0, tzinfo=UTC)
    winner = ClassroomPlanSuggestionJob(
        id="winner-job",
        authoring_session_id=None,
        teacher_id="teacher-1",
        space_id="space-1",
        parent_algorithm_id="parent-1",
        request_hash="a" * 64,
        suggestion_input={"title": "", "statement": "实现字典查询"},
        result=None,
        run_at=now,
        status="pending",
        active_slot=1,
        lease_owner=None,
        lease_expires_at=None,
        attempts=0,
        failure_code=None,
        completed_at=None,
        created_at=now,
        updated_at=now,
    )

    class RaceSession:
        scalar_calls = 0

        def scalar(self, _statement):
            self.scalar_calls += 1
            return None if self.scalar_calls == 1 else winner

        def add(self, _job):
            return None

        def flush(self):
            raise IntegrityError("duplicate", {}, RuntimeError("unique race"))

        @contextmanager
        def begin_nested(self):
            yield self

    race_session = RaceSession()

    class RaceFactory:
        @contextmanager
        def begin(self):
            yield race_session

    service = PlanSuggestionJobService(
        RaceFactory(),  # type: ignore[arg-type]
        RecordingGenerator(),
        clock=lambda: now,
    )

    result = service.submit(
        teacher_id="teacher-1",
        space_id="space-1",
        parent_algorithm_id="parent-1",
        suggestion_input=PlanSuggestionInput(title="", statement="实现字典查询"),
    )

    assert result.job_id == "winner-job"
    assert result.status == "pending"


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
    assert job.authoring_session_id is None
    assert job.request_hash == submitted.input_hash
    assert job.suggestion_input == {}
    assert job.result == {
        "title": "字典课堂练习",
        "knowledge_points": [{"name": "字典读取", "description": "按键读取并验证结果。"}],
    }


def test_worker_returns_a_safe_failure_after_retry_budget_and_erases_input() -> None:
    now = datetime(2026, 8, 19, 10, 0, tzinfo=UTC)
    service, generator, session_factory = build_service(now, retry_provider_errors=True)
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
    assert job.authoring_session_id is None
    assert job.request_hash == submitted.input_hash
    assert job.suggestion_input == {}
    assert job.result is None


def test_standard_profile_provider_failure_is_terminal_after_one_call() -> None:
    now = datetime(2026, 8, 19, 10, 0, tzinfo=UTC)
    service, generator, session_factory = build_service(now)
    generator.error = UpstreamUnavailableError("ai_provider_timeout", retryable=True)
    submitted = service.submit(
        teacher_id="teacher-1",
        space_id="space-1",
        parent_algorithm_id="parent-1",
        suggestion_input=PlanSuggestionInput(title="", statement="实现字典查询"),
    )

    assert service.run_due_jobs("worker-a") == 1

    failed = service.get_for_teacher(submitted.job_id, teacher_id="teacher-1")
    assert failed.status == "failed"
    assert failed.failure_code == "ai_provider_timeout"
    assert len(generator.calls) == 1
    with session_factory() as session:
        job = session.get(ClassroomPlanSuggestionJob, submitted.job_id)
    assert job is not None
    assert job.attempts == 1
    assert job.suggestion_input == {}


def test_worker_claims_one_job_and_keeps_lease_for_full_provider_budget() -> None:
    now = datetime(2026, 8, 19, 10, 0, tzinfo=UTC)
    service, _generator, _session_factory = build_service(now)
    for statement in ("实现字典查询", "实现字典遍历"):
        service.submit(
            teacher_id="teacher-1",
            space_id="space-1",
            parent_algorithm_id="parent-1",
            suggestion_input=PlanSuggestionInput(title="", statement=statement),
        )

    claimed = service.claim_due_jobs("worker-a")

    assert len(claimed) == 1
    claimed_by_second_worker = service.claim_due_jobs(
        "worker-b", now + timedelta(seconds=1_440)
    )
    assert len(claimed_by_second_worker) == 1
    assert claimed_by_second_worker[0].id != claimed[0].id


def test_expired_lease_at_attempt_limit_fails_without_another_provider_call() -> None:
    now = datetime(2026, 8, 19, 10, 0, tzinfo=UTC)
    service, generator, session_factory = build_service(now)
    submitted = service.submit(
        teacher_id="teacher-1",
        space_id="space-1",
        parent_algorithm_id="parent-1",
        suggestion_input=PlanSuggestionInput(title="", statement="实现字典查询"),
    )
    claimed = service.claim_due_jobs("crashed-worker")
    assert len(claimed) == 1
    with session_factory.begin() as session:
        job = session.get(ClassroomPlanSuggestionJob, submitted.job_id)
        assert job is not None
        job.attempts = 2
        lease_expires_at = job.lease_expires_at
    assert lease_expires_at is not None

    assert service.claim_due_jobs("recovery-worker", lease_expires_at) == ()

    failed = service.get_for_teacher(submitted.job_id, teacher_id="teacher-1")
    assert failed.status == "failed"
    assert failed.failure_code == "ai_suggestion_attempts_exhausted"
    assert generator.calls == []
    with session_factory() as session:
        job = session.get(ClassroomPlanSuggestionJob, submitted.job_id)
    assert job is not None
    assert job.attempts == 2
    assert job.suggestion_input == {}


def test_worker_tick_survives_a_lease_lost_after_provider_completion(monkeypatch) -> None:
    now = datetime(2026, 8, 19, 10, 0, tzinfo=UTC)
    service, _generator, _session_factory = build_service(now)
    service.submit(
        teacher_id="teacher-1",
        space_id="space-1",
        parent_algorithm_id="parent-1",
        suggestion_input=PlanSuggestionInput(title="", statement="实现字典查询"),
    )

    def lose_lease(*_args, **_kwargs):
        raise AuthorizationError("plan_suggestion_job_lease_not_owned")

    monkeypatch.setattr(service, "complete", lose_lease)

    assert service.run_due_jobs("worker-a") == 1


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
