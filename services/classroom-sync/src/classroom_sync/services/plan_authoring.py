"""Teacher-owned authoring sessions with one user-visible AI suggestion attempt."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, cast
from uuid import uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from classroom_sync.errors import (
    AiSuggestionUnavailableError,
    AuthorizationError,
    ConflictError,
    NotFoundError,
    UpstreamUnavailableError,
)
from classroom_sync.models import ClassroomPlanSuggestionJob, PlanAuthoringSession
from classroom_sync.repositories import ClassroomRepository
from classroom_sync.services.plan_suggestion_jobs import (
    PlanSuggestionJobService,
    PlanSuggestionJobSnapshot,
)
from classroom_sync.services.plan_suggestions import PlanSuggestion, PlanSuggestionInput

AuthoringStatus = Literal["open", "published", "abandoned"]
AuthoringSuggestionStatus = Literal["not_requested", "pending", "ready", "failed"]


@dataclass(frozen=True)
class AuthoringSuggestionSnapshot:
    status: AuthoringSuggestionStatus
    job_id: str | None
    input_hash: str | None
    suggestion: PlanSuggestion | None
    failure_code: str | None


@dataclass(frozen=True)
class PlanAuthoringSnapshot:
    authoring_session_id: str
    status: AuthoringStatus
    space_id: str
    parent_algorithm_id: str
    draft_id: str | None
    suggestion: AuthoringSuggestionSnapshot


class PlanAuthoringService:
    """Own the transaction boundary that binds one suggestion job to one session."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        suggestion_jobs: PlanSuggestionJobService | None,
        *,
        clock: Callable[[], datetime],
    ) -> None:
        self._session_factory = session_factory
        self._suggestion_jobs = suggestion_jobs
        self._clock = clock

    def create_or_return_open(
        self,
        *,
        teacher_id: str,
        space_id: str,
        parent_algorithm_id: str,
    ) -> PlanAuthoringSnapshot:
        now = self._clock()
        with self._session_factory.begin() as session:
            repository = ClassroomRepository(session)
            existing = repository.find_open_authoring_session(
                teacher_id=teacher_id,
                space_id=space_id,
                parent_algorithm_id=parent_algorithm_id,
                for_update=True,
            )
            if existing is not None:
                return self._snapshot(repository, existing)

            authoring = PlanAuthoringSession(
                id=str(uuid4()),
                teacher_id=teacher_id,
                space_id=space_id,
                parent_algorithm_id=parent_algorithm_id,
                status="open",
                active_slot=1,
                suggestion_job_id=None,
                published_plan_id=None,
                created_at=now,
                updated_at=now,
                closed_at=None,
            )
            try:
                with session.begin_nested():
                    session.add(authoring)
                    session.flush()
            except IntegrityError:
                winner = repository.find_open_authoring_session(
                    teacher_id=teacher_id,
                    space_id=space_id,
                    parent_algorithm_id=parent_algorithm_id,
                    for_update=True,
                )
                if winner is None:
                    raise
                return self._snapshot(repository, winner)
            return self._snapshot(repository, authoring)

    def request_suggestion(
        self,
        authoring_session_id: str,
        *,
        teacher_id: str,
        suggestion_input: PlanSuggestionInput,
    ) -> PlanAuthoringSnapshot:
        with self._session_factory.begin() as session:
            repository = ClassroomRepository(session)
            authoring = self._owned_locked_session(
                repository, authoring_session_id, teacher_id=teacher_id
            )
            if authoring.status != "open":
                raise ConflictError("plan_authoring_session_closed")

            # The durable link is authoritative.  In particular, do not hash or
            # compare a duplicate POST payload after the first job is linked.
            if authoring.suggestion_job_id is not None:
                return self._snapshot(repository, authoring)

            if self._suggestion_jobs is None:
                raise AiSuggestionUnavailableError("ai_suggestion_not_configured")

            job = self._suggestion_jobs.submit(
                authoring_session_id=authoring.id,
                teacher_id=teacher_id,
                space_id=authoring.space_id,
                parent_algorithm_id=authoring.parent_algorithm_id,
                suggestion_input=suggestion_input,
                session=session,
            )
            authoring.suggestion_job_id = job.job_id
            authoring.updated_at = self._clock()
            return self._snapshot(repository, authoring, suggestion_job=job)

    def get_current(
        self,
        *,
        teacher_id: str,
        space_id: str,
        parent_algorithm_id: str,
    ) -> PlanAuthoringSnapshot:
        """Return the existing open session without creating one during a read."""

        with self._session_factory() as session:
            repository = ClassroomRepository(session)
            authoring = repository.find_open_authoring_session(
                teacher_id=teacher_id,
                space_id=space_id,
                parent_algorithm_id=parent_algorithm_id,
            )
            if authoring is None:
                raise NotFoundError("plan_authoring_session_not_found")
            return self._snapshot(repository, authoring)

    def get_owned(
        self, authoring_session_id: str, *, teacher_id: str
    ) -> PlanAuthoringSnapshot:
        """Read one session only after checking its durable teacher owner."""

        with self._session_factory() as session:
            repository = ClassroomRepository(session)
            authoring = repository.get_authoring_session(authoring_session_id)
            if authoring is None:
                raise NotFoundError("plan_authoring_session_not_found")
            if authoring.teacher_id != teacher_id:
                raise AuthorizationError("plan_authoring_session_not_owned")
            return self._snapshot(repository, authoring)

    def get_owned_parent_scope(
        self, authoring_session_id: str, *, teacher_id: str
    ) -> tuple[str, str]:
        """Return only the owned parent key needed for current authorization."""

        with self._session_factory() as session:
            authoring = ClassroomRepository(session).get_authoring_session(
                authoring_session_id
            )
            if authoring is None:
                raise NotFoundError("plan_authoring_session_not_found")
            if authoring.teacher_id != teacher_id:
                raise AuthorizationError("plan_authoring_session_not_owned")
            return authoring.space_id, authoring.parent_algorithm_id

    def abandon(
        self, authoring_session_id: str, *, teacher_id: str
    ) -> PlanAuthoringSnapshot:
        now = self._clock()
        with self._session_factory.begin() as session:
            repository = ClassroomRepository(session)
            authoring = self._owned_locked_session(
                repository, authoring_session_id, teacher_id=teacher_id
            )
            if authoring.status == "open":
                if authoring.suggestion_job_id is not None:
                    linked_job = self._linked_job(repository, authoring, for_update=True)
                    if self._suggestion_jobs is None:
                        self._cancel_linked_job_without_provider(linked_job, now=now)
                    else:
                        self._suggestion_jobs.cancel_for_authoring_session(
                            authoring.id,
                            teacher_id=teacher_id,
                            space_id=authoring.space_id,
                            parent_algorithm_id=authoring.parent_algorithm_id,
                            session=session,
                        )
                authoring.status = "abandoned"
                authoring.active_slot = None
                authoring.closed_at = now
                authoring.updated_at = now
            return self._snapshot(repository, authoring)

    @staticmethod
    def _cancel_linked_job_without_provider(
        job: ClassroomPlanSuggestionJob, *, now: datetime
    ) -> None:
        """Keep abandon available if optional AI configuration is later removed."""

        if job.status in {"ready", "failed"}:
            return
        job.suggestion_input = {}
        job.result = None
        job.status = "failed"
        job.active_slot = None
        job.lease_owner = None
        job.lease_expires_at = None
        job.failure_code = "ai_suggestion_authoring_abandoned"
        job.completed_at = now
        job.updated_at = now

    @staticmethod
    def _owned_locked_session(
        repository: ClassroomRepository,
        authoring_session_id: str,
        *,
        teacher_id: str,
    ) -> PlanAuthoringSession:
        authoring = repository.get_authoring_session(
            authoring_session_id, for_update=True
        )
        if authoring is None:
            raise NotFoundError("plan_authoring_session_not_found")
        if authoring.teacher_id != teacher_id:
            raise AuthorizationError("plan_authoring_session_not_owned")
        return authoring

    @classmethod
    def _snapshot(
        cls,
        repository: ClassroomRepository,
        authoring: PlanAuthoringSession,
        *,
        suggestion_job: PlanSuggestionJobSnapshot | None = None,
    ) -> PlanAuthoringSnapshot:
        if authoring.status not in {"open", "published", "abandoned"}:
            raise UpstreamUnavailableError(
                "plan_authoring_session_status_invalid", retryable=False
            )
        draft = repository.get_plan_draft_for_authoring_session(authoring.id)
        suggestion = AuthoringSuggestionSnapshot(
            status="not_requested",
            job_id=None,
            input_hash=None,
            suggestion=None,
            failure_code=None,
        )
        if authoring.suggestion_job_id is not None:
            if suggestion_job is None:
                job_model = cls._linked_job(repository, authoring)
                suggestion_job = PlanSuggestionJobService.snapshot_for_model(job_model)
            suggestion = cls._suggestion_snapshot(suggestion_job)
        return PlanAuthoringSnapshot(
            authoring_session_id=authoring.id,
            status=cast(AuthoringStatus, authoring.status),
            space_id=authoring.space_id,
            parent_algorithm_id=authoring.parent_algorithm_id,
            draft_id=None if draft is None else draft.id,
            suggestion=suggestion,
        )

    @staticmethod
    def _linked_job(
        repository: ClassroomRepository,
        authoring: PlanAuthoringSession,
        *,
        for_update: bool = False,
    ) -> ClassroomPlanSuggestionJob:
        job_id = authoring.suggestion_job_id
        if job_id is None:
            raise UpstreamUnavailableError(
                "plan_authoring_suggestion_job_missing", retryable=False
            )
        job = repository.get_plan_suggestion_job(job_id, for_update=for_update)
        if job is None:
            raise UpstreamUnavailableError(
                "plan_authoring_suggestion_job_missing", retryable=False
            )
        if (
            job.teacher_id != authoring.teacher_id
            or job.space_id != authoring.space_id
            or job.parent_algorithm_id != authoring.parent_algorithm_id
            or job.authoring_session_id != authoring.id
        ):
            raise UpstreamUnavailableError(
                "plan_authoring_suggestion_job_invalid", retryable=False
            )
        return job

    @staticmethod
    def _suggestion_snapshot(
        job: PlanSuggestionJobSnapshot,
    ) -> AuthoringSuggestionSnapshot:
        return AuthoringSuggestionSnapshot(
            status=job.status,
            job_id=job.job_id,
            input_hash=job.input_hash,
            suggestion=job.suggestion,
            failure_code=job.failure_code,
        )
