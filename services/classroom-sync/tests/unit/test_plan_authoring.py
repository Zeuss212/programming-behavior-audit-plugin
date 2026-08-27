"""Session boundaries for exactly one teacher-visible AI suggestion attempt."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from classroom_sync.errors import AuthorizationError
from classroom_sync.models import Base, ClassroomPlanSuggestionJob, PlanAuthoringSession
from classroom_sync.services.plan_authoring import PlanAuthoringService
from classroom_sync.services.plan_suggestion_jobs import PlanSuggestionJobService
from classroom_sync.services.plan_suggestions import (
    PlanSuggestion,
    PlanSuggestionInput,
    SuggestedKnowledgePoint,
)


class RecordingGenerator:
    retry_provider_errors = False

    def __init__(self) -> None:
        self.calls: list[PlanSuggestionInput] = []

    def generate(self, suggestion_input: PlanSuggestionInput) -> PlanSuggestion:
        self.calls.append(suggestion_input)
        return PlanSuggestion(
            title="字典课堂练习",
            knowledge_points=(
                SuggestedKnowledgePoint(name="字典读取", description="按键读取并验证结果。"),
            ),
        )


def build_services(now: datetime):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    generator = RecordingGenerator()
    jobs = PlanSuggestionJobService(session_factory, generator, clock=lambda: now)
    authoring = PlanAuthoringService(session_factory, jobs, clock=lambda: now)
    return authoring, jobs, generator, session_factory


def test_create_returns_the_existing_open_session_for_the_same_owner_scope() -> None:
    now = datetime(2026, 8, 27, 9, 0, tzinfo=UTC)
    service, _jobs, _generator, session_factory = build_services(now)

    first = service.create_or_return_open(
        teacher_id="teacher-1", space_id="space-1", parent_algorithm_id="parent-1"
    )
    second = service.create_or_return_open(
        teacher_id="teacher-1", space_id="space-1", parent_algorithm_id="parent-1"
    )

    assert second == first
    assert first.status == "open"
    assert first.draft_id is None
    assert first.suggestion.status == "not_requested"
    with session_factory() as session:
        sessions = list(session.scalars(select(PlanAuthoringSession)))
    assert len(sessions) == 1


def test_create_recovers_the_concurrent_insert_winner(monkeypatch) -> None:
    now = datetime(2026, 8, 27, 9, 0, tzinfo=UTC)
    winner = PlanAuthoringSession(
        id="winner-session",
        teacher_id="teacher-1",
        space_id="space-1",
        parent_algorithm_id="parent-1",
        status="open",
        active_slot=1,
        suggestion_job_id=None,
        published_plan_id=None,
        created_at=now,
        updated_at=now,
        closed_at=None,
    )

    class RaceSession:
        def add(self, _authoring_session: PlanAuthoringSession) -> None:
            return None

        def flush(self) -> None:
            raise IntegrityError("duplicate", {}, RuntimeError("unique race"))

        @contextmanager
        def begin_nested(self):
            yield self

    class RaceFactory:
        @contextmanager
        def begin(self):
            yield RaceSession()

    class RaceRepository:
        calls = 0

        def __init__(self, _session: object) -> None:
            return None

        def find_open_authoring_session(self, **_kwargs):
            self.calls += 1
            return None if self.calls == 1 else winner

        def get_plan_draft_for_authoring_session(self, _session_id: str):
            return None

    monkeypatch.setattr("classroom_sync.services.plan_authoring.ClassroomRepository", RaceRepository)
    service = PlanAuthoringService(
        RaceFactory(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        clock=lambda: now,
    )

    snapshot = service.create_or_return_open(
        teacher_id="teacher-1", space_id="space-1", parent_algorithm_id="parent-1"
    )

    assert snapshot.authoring_session_id == "winner-session"
    assert snapshot.status == "open"


def test_open_sessions_are_isolated_between_teachers() -> None:
    now = datetime(2026, 8, 27, 9, 0, tzinfo=UTC)
    service, _jobs, _generator, _session_factory = build_services(now)

    first = service.create_or_return_open(
        teacher_id="teacher-1", space_id="space-1", parent_algorithm_id="parent-1"
    )
    second = service.create_or_return_open(
        teacher_id="teacher-2", space_id="space-1", parent_algorithm_id="parent-1"
    )

    assert first.authoring_session_id != second.authoring_session_id


def test_abandon_closes_the_session_and_only_then_allows_a_new_one() -> None:
    now = datetime(2026, 8, 27, 9, 0, tzinfo=UTC)
    service, _jobs, _generator, _session_factory = build_services(now)
    opened = service.create_or_return_open(
        teacher_id="teacher-1", space_id="space-1", parent_algorithm_id="parent-1"
    )

    abandoned = service.abandon(opened.authoring_session_id, teacher_id="teacher-1")
    reopened = service.create_or_return_open(
        teacher_id="teacher-1", space_id="space-1", parent_algorithm_id="parent-1"
    )

    assert abandoned.status == "abandoned"
    assert reopened.status == "open"
    assert reopened.authoring_session_id != opened.authoring_session_id


def test_abandon_releases_a_pending_attempt_before_a_new_session_requests_again() -> None:
    now = datetime(2026, 8, 27, 9, 0, tzinfo=UTC)
    service, _jobs, _generator, _session_factory = build_services(now)
    opened = service.create_or_return_open(
        teacher_id="teacher-1", space_id="space-1", parent_algorithm_id="parent-1"
    )
    first = service.request_suggestion(
        opened.authoring_session_id,
        teacher_id="teacher-1",
        suggestion_input=PlanSuggestionInput(title="", statement="实现字典查询"),
    )
    service.abandon(opened.authoring_session_id, teacher_id="teacher-1")
    reopened = service.create_or_return_open(
        teacher_id="teacher-1", space_id="space-1", parent_algorithm_id="parent-1"
    )

    second = service.request_suggestion(
        reopened.authoring_session_id,
        teacher_id="teacher-1",
        suggestion_input=PlanSuggestionInput(title="", statement="实现字典查询"),
    )

    assert second.suggestion.status == "pending"
    assert second.suggestion.job_id != first.suggestion.job_id


def test_first_suggestion_request_links_one_job_and_public_input_hash() -> None:
    now = datetime(2026, 8, 27, 9, 0, tzinfo=UTC)
    service, _jobs, _generator, session_factory = build_services(now)
    opened = service.create_or_return_open(
        teacher_id="teacher-1", space_id="space-1", parent_algorithm_id="parent-1"
    )

    requested = service.request_suggestion(
        opened.authoring_session_id,
        teacher_id="teacher-1",
        suggestion_input=PlanSuggestionInput(title="", statement="实现字典查询"),
    )

    assert requested.suggestion.status == "pending"
    assert requested.suggestion.job_id is not None
    assert (
        requested.suggestion.input_hash
        == "c97b3366eadd18ad59c168bb99b2a2032f83315d84d65487301e5b8bd6af5941"
    )
    with session_factory() as session:
        authoring = session.get(PlanAuthoringSession, opened.authoring_session_id)
        job = session.get(ClassroomPlanSuggestionJob, requested.suggestion.job_id)
    assert authoring is not None
    assert authoring.suggestion_job_id == requested.suggestion.job_id
    assert job is not None
    assert job.authoring_session_id == opened.authoring_session_id


def test_duplicate_request_with_different_text_recovers_the_original_job_and_hash() -> None:
    now = datetime(2026, 8, 27, 9, 0, tzinfo=UTC)
    service, _jobs, _generator, session_factory = build_services(now)
    opened = service.create_or_return_open(
        teacher_id="teacher-1", space_id="space-1", parent_algorithm_id="parent-1"
    )
    first = service.request_suggestion(
        opened.authoring_session_id,
        teacher_id="teacher-1",
        suggestion_input=PlanSuggestionInput(title="", statement="实现字典查询"),
    )

    duplicate = service.request_suggestion(
        opened.authoring_session_id,
        teacher_id="teacher-1",
        suggestion_input=PlanSuggestionInput(title="不同标题", statement="完全不同的要求"),
    )

    assert duplicate.suggestion == first.suggestion
    with session_factory() as session:
        jobs = list(session.scalars(select(ClassroomPlanSuggestionJob)))
    assert len(jobs) == 1
    assert jobs[0].suggestion_input["statement"] == "实现字典查询"


def test_duplicate_request_recovers_the_ready_result_without_a_second_provider_attempt() -> None:
    now = datetime(2026, 8, 27, 9, 0, tzinfo=UTC)
    service, jobs, generator, _session_factory = build_services(now)
    opened = service.create_or_return_open(
        teacher_id="teacher-1", space_id="space-1", parent_algorithm_id="parent-1"
    )
    first = service.request_suggestion(
        opened.authoring_session_id,
        teacher_id="teacher-1",
        suggestion_input=PlanSuggestionInput(title="", statement="实现字典查询"),
    )
    assert jobs.run_due_jobs("worker-a") == 1

    recovered = service.request_suggestion(
        opened.authoring_session_id,
        teacher_id="teacher-1",
        suggestion_input=PlanSuggestionInput(title="", statement="不应被提交"),
    )

    assert recovered.suggestion.status == "ready"
    assert recovered.suggestion.job_id == first.suggestion.job_id
    assert recovered.suggestion.suggestion is not None
    assert recovered.suggestion.suggestion.title == "字典课堂练习"
    assert len(generator.calls) == 1


def test_terminal_failure_consumes_the_session_attempt() -> None:
    now = datetime(2026, 8, 27, 9, 0, tzinfo=UTC)
    service, jobs, generator, _session_factory = build_services(now)
    opened = service.create_or_return_open(
        teacher_id="teacher-1", space_id="space-1", parent_algorithm_id="parent-1"
    )
    first = service.request_suggestion(
        opened.authoring_session_id,
        teacher_id="teacher-1",
        suggestion_input=PlanSuggestionInput(title="", statement="实现字典查询"),
    )
    claimed = jobs.claim_due_jobs("worker-a")
    assert len(claimed) == 1
    jobs.record_failure(
        claimed[0].id,
        worker_id="worker-a",
        failure_code="ai_suggestion_response_invalid",
        retry_delay=None,
    )

    recovered = service.request_suggestion(
        opened.authoring_session_id,
        teacher_id="teacher-1",
        suggestion_input=PlanSuggestionInput(title="", statement="再试一次"),
    )

    assert recovered.suggestion.status == "failed"
    assert recovered.suggestion.job_id == first.suggestion.job_id
    assert recovered.suggestion.failure_code == "ai_suggestion_response_invalid"
    assert generator.calls == []


@pytest.mark.parametrize("operation", ["request", "abandon"])
def test_authoring_session_owner_mismatch_is_rejected(operation: str) -> None:
    now = datetime(2026, 8, 27, 9, 0, tzinfo=UTC)
    service, _jobs, _generator, session_factory = build_services(now)
    opened = service.create_or_return_open(
        teacher_id="teacher-1", space_id="space-1", parent_algorithm_id="parent-1"
    )

    with pytest.raises(AuthorizationError, match="plan_authoring_session_not_owned"):
        if operation == "request":
            service.request_suggestion(
                opened.authoring_session_id,
                teacher_id="teacher-2",
                suggestion_input=PlanSuggestionInput(title="", statement="窃取建议"),
            )
        else:
            service.abandon(opened.authoring_session_id, teacher_id="teacher-2")

    with session_factory() as session:
        jobs = list(session.scalars(select(ClassroomPlanSuggestionJob)))
    assert jobs == []
