"""Durable, owner-scoped jobs for bounded AI classroom-plan suggestions."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal, Protocol
from uuid import uuid4

from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import Select, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from classroom_sync.errors import (
    AuthorizationError,
    ConflictError,
    NotFoundError,
    UpstreamUnavailableError,
)
from classroom_sync.models import ClassroomPlanSuggestionJob
from classroom_sync.services.plan_suggestions import (
    PlanSuggestion,
    PlanSuggestionInput,
    PlanSuggestionPayload,
)


class PlanSuggestionGenerator(Protocol):
    """One server-only provider capability used by the durable worker."""

    @property
    def retry_provider_errors(self) -> bool: ...

    def generate(self, suggestion_input: PlanSuggestionInput) -> PlanSuggestion: ...


PlanSuggestionJobStatus = Literal["pending", "ready", "failed"]


@dataclass(frozen=True)
class PlanSuggestionJobSnapshot:
    """The only owner-visible status representation; never includes source text."""

    job_id: str
    status: PlanSuggestionJobStatus
    failure_code: str | None
    suggestion: PlanSuggestion | None
    input_hash: str | None = None


class PlanSuggestionJobService:
    """Lease and execute suggestions without holding an HTTP connection open."""

    _RETRY_DELAYS = (timedelta(seconds=5), timedelta(seconds=30))
    # One Coding Plan attempt may make two HTTP calls.  httpx applies the
    # configured timeout to each connect/write/read/pool phase, so retain the
    # lease for the complete worst-case operation budget plus one minute.
    _LEASE_SECONDS = 1_500

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        suggestion_service: PlanSuggestionGenerator,
        *,
        clock: Callable[[], datetime],
        max_attempts: int = 3,
    ) -> None:
        if not 1 <= max_attempts <= 3:
            raise ValueError("max_attempts must be between 1 and 3")
        self._session_factory = session_factory
        self._suggestion_service = suggestion_service
        self._clock = clock
        self._max_attempts = max_attempts

    def submit(
        self,
        *,
        authoring_session_id: str | None = None,
        teacher_id: str,
        space_id: str,
        parent_algorithm_id: str,
        suggestion_input: PlanSuggestionInput,
        session: Session | None = None,
    ) -> PlanSuggestionJobSnapshot:
        """Create one active job, or reuse an identical active request safely."""

        if session is not None:
            return self._submit_in_session(
                session,
                authoring_session_id=authoring_session_id,
                teacher_id=teacher_id,
                space_id=space_id,
                parent_algorithm_id=parent_algorithm_id,
                suggestion_input=suggestion_input,
            )
        with self._session_factory.begin() as owned_session:
            return self._submit_in_session(
                owned_session,
                authoring_session_id=authoring_session_id,
                teacher_id=teacher_id,
                space_id=space_id,
                parent_algorithm_id=parent_algorithm_id,
                suggestion_input=suggestion_input,
            )

    def _submit_in_session(
        self,
        session: Session,
        *,
        authoring_session_id: str | None,
        teacher_id: str,
        space_id: str,
        parent_algorithm_id: str,
        suggestion_input: PlanSuggestionInput,
    ) -> PlanSuggestionJobSnapshot:
        """Insert inside the caller's locked authoring transaction when supplied."""

        now = self._utc_now()
        source = suggestion_input.model_dump(mode="json")
        request_hash = self.input_hash(suggestion_input)
        existing_statement = select(ClassroomPlanSuggestionJob)
        if authoring_session_id is not None:
            existing_statement = existing_statement.where(
                ClassroomPlanSuggestionJob.authoring_session_id == authoring_session_id
            )
        else:
            existing_statement = existing_statement.where(
                ClassroomPlanSuggestionJob.teacher_id == teacher_id,
                ClassroomPlanSuggestionJob.space_id == space_id,
                ClassroomPlanSuggestionJob.parent_algorithm_id == parent_algorithm_id,
                ClassroomPlanSuggestionJob.request_hash == request_hash,
                ClassroomPlanSuggestionJob.active_slot == 1,
            )
        existing = session.scalar(existing_statement.with_for_update())
        if existing is not None:
            self._verify_owner_scope(
                existing,
                teacher_id=teacher_id,
                space_id=space_id,
                parent_algorithm_id=parent_algorithm_id,
            )
            return self._snapshot(existing)

        if authoring_session_id is not None:
            legacy = session.scalar(
                self._active_request_statement(
                    teacher_id=teacher_id,
                    space_id=space_id,
                    parent_algorithm_id=parent_algorithm_id,
                    request_hash=request_hash,
                    unlinked_only=True,
                ).with_for_update()
            )
            if legacy is not None:
                return self._adopt_legacy_job(
                    session,
                    legacy,
                    authoring_session_id=authoring_session_id,
                    teacher_id=teacher_id,
                    space_id=space_id,
                    parent_algorithm_id=parent_algorithm_id,
                )

        job = ClassroomPlanSuggestionJob(
            id=str(uuid4()),
            authoring_session_id=authoring_session_id,
            teacher_id=teacher_id,
            space_id=space_id,
            parent_algorithm_id=parent_algorithm_id,
            request_hash=request_hash,
            suggestion_input=source,
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
        try:
            with session.begin_nested():
                session.add(job)
                session.flush()
        except IntegrityError:
            if authoring_session_id is not None:
                winner = session.scalar(
                    select(ClassroomPlanSuggestionJob)
                    .where(
                        ClassroomPlanSuggestionJob.authoring_session_id
                        == authoring_session_id
                    )
                    .with_for_update()
                )
                if winner is not None:
                    self._verify_owner_scope(
                        winner,
                        teacher_id=teacher_id,
                        space_id=space_id,
                        parent_algorithm_id=parent_algorithm_id,
                    )
                    return self._snapshot(winner)

                legacy = session.scalar(
                    self._active_request_statement(
                        teacher_id=teacher_id,
                        space_id=space_id,
                        parent_algorithm_id=parent_algorithm_id,
                        request_hash=request_hash,
                        unlinked_only=True,
                    ).with_for_update()
                )
                if legacy is not None:
                    return self._adopt_legacy_job(
                        session,
                        legacy,
                        authoring_session_id=authoring_session_id,
                        teacher_id=teacher_id,
                        space_id=space_id,
                        parent_algorithm_id=parent_algorithm_id,
                    )

                collision = session.scalar(
                    self._active_request_statement(
                        teacher_id=teacher_id,
                        space_id=space_id,
                        parent_algorithm_id=parent_algorithm_id,
                        request_hash=request_hash,
                        unlinked_only=False,
                        active_status_only=False,
                    ).with_for_update()
                )
                if collision is not None:
                    raise ConflictError("plan_suggestion_active_request_conflict")
                raise
            else:
                winner = session.scalar(
                    select(ClassroomPlanSuggestionJob).where(
                        ClassroomPlanSuggestionJob.teacher_id == teacher_id,
                        ClassroomPlanSuggestionJob.space_id == space_id,
                        ClassroomPlanSuggestionJob.parent_algorithm_id
                        == parent_algorithm_id,
                        ClassroomPlanSuggestionJob.request_hash == request_hash,
                        ClassroomPlanSuggestionJob.active_slot == 1,
                    )
                )
            if winner is None:
                raise
            self._verify_owner_scope(
                winner,
                teacher_id=teacher_id,
                space_id=space_id,
                parent_algorithm_id=parent_algorithm_id,
            )
            return self._snapshot(winner)
        return self._snapshot(job)

    def get_for_teacher(self, job_id: str, *, teacher_id: str) -> PlanSuggestionJobSnapshot:
        with self._session_factory() as session:
            job = session.get(ClassroomPlanSuggestionJob, job_id)
            if job is None:
                raise NotFoundError("plan_suggestion_job_not_found")
            if job.teacher_id != teacher_id:
                raise AuthorizationError("plan_suggestion_job_not_owned")
            return self._snapshot(job)

    def cancel_for_authoring_session(
        self,
        authoring_session_id: str,
        *,
        teacher_id: str,
        space_id: str,
        parent_algorithm_id: str,
        session: Session,
    ) -> None:
        """Make an abandoned session's unfinished job terminal and source-free."""

        job = session.scalar(
            select(ClassroomPlanSuggestionJob)
            .where(ClassroomPlanSuggestionJob.authoring_session_id == authoring_session_id)
            .with_for_update()
        )
        if job is None:
            return
        if job.authoring_session_id != authoring_session_id:
            raise AuthorizationError("plan_suggestion_job_not_owned")
        self._verify_owner_scope(
            job,
            teacher_id=teacher_id,
            space_id=space_id,
            parent_algorithm_id=parent_algorithm_id,
        )
        if job.status in {"ready", "failed"}:
            return
        now = self._utc_now()
        job.suggestion_input = {}
        job.result = None
        job.status = "failed"
        job.active_slot = None
        job.lease_owner = None
        job.lease_expires_at = None
        job.failure_code = "ai_suggestion_authoring_abandoned"
        job.completed_at = now
        job.updated_at = now

    @classmethod
    def snapshot_for_model(
        cls, job: ClassroomPlanSuggestionJob
    ) -> PlanSuggestionJobSnapshot:
        """Project one already-loaded job without opening a second transaction."""

        return cls._snapshot(job)

    @staticmethod
    def _verify_owner_scope(
        job: ClassroomPlanSuggestionJob,
        *,
        teacher_id: str,
        space_id: str,
        parent_algorithm_id: str,
    ) -> None:
        if (
            job.teacher_id != teacher_id
            or job.space_id != space_id
            or job.parent_algorithm_id != parent_algorithm_id
        ):
            raise AuthorizationError("plan_suggestion_job_not_owned")

    @staticmethod
    def _active_request_statement(
        *,
        teacher_id: str,
        space_id: str,
        parent_algorithm_id: str,
        request_hash: str,
        unlinked_only: bool,
        active_status_only: bool = True,
    ) -> Select[tuple[ClassroomPlanSuggestionJob]]:
        statement = select(ClassroomPlanSuggestionJob).where(
            ClassroomPlanSuggestionJob.teacher_id == teacher_id,
            ClassroomPlanSuggestionJob.space_id == space_id,
            ClassroomPlanSuggestionJob.parent_algorithm_id == parent_algorithm_id,
            ClassroomPlanSuggestionJob.request_hash == request_hash,
            ClassroomPlanSuggestionJob.active_slot == 1,
        )
        if active_status_only:
            statement = statement.where(
                ClassroomPlanSuggestionJob.status.in_(("pending", "leased"))
            )
        if unlinked_only:
            statement = statement.where(
                ClassroomPlanSuggestionJob.authoring_session_id.is_(None)
            )
        return statement

    def _adopt_legacy_job(
        self,
        session: Session,
        job: ClassroomPlanSuggestionJob,
        *,
        authoring_session_id: str,
        teacher_id: str,
        space_id: str,
        parent_algorithm_id: str,
    ) -> PlanSuggestionJobSnapshot:
        self._verify_owner_scope(
            job,
            teacher_id=teacher_id,
            space_id=space_id,
            parent_algorithm_id=parent_algorithm_id,
        )
        if job.authoring_session_id is not None:
            raise ConflictError("plan_suggestion_active_request_conflict")
        try:
            with session.begin_nested():
                job.authoring_session_id = authoring_session_id
                session.flush()
        except IntegrityError:
            winner = session.scalar(
                select(ClassroomPlanSuggestionJob)
                .where(
                    ClassroomPlanSuggestionJob.authoring_session_id
                    == authoring_session_id
                )
                .with_for_update()
            )
            if winner is None:
                raise ConflictError("plan_suggestion_active_request_conflict")
            self._verify_owner_scope(
                winner,
                teacher_id=teacher_id,
                space_id=space_id,
                parent_algorithm_id=parent_algorithm_id,
            )
            return self._snapshot(winner)
        return self._snapshot(job)

    def claim_due_jobs(
        self, worker_id: str, now: datetime | None = None
    ) -> tuple[ClassroomPlanSuggestionJob, ...]:
        claim_time = self._utc_now() if now is None else self._as_utc(now)
        with self._session_factory.begin() as session:
            exhausted_jobs = list(
                session.scalars(
                    select(ClassroomPlanSuggestionJob)
                    .where(
                        ClassroomPlanSuggestionJob.run_at <= claim_time,
                        ClassroomPlanSuggestionJob.status.in_(("pending", "leased")),
                        ClassroomPlanSuggestionJob.attempts >= self._max_attempts,
                        or_(
                            ClassroomPlanSuggestionJob.lease_expires_at.is_(None),
                            ClassroomPlanSuggestionJob.lease_expires_at <= claim_time,
                        ),
                    )
                    .with_for_update(skip_locked=True)
                )
            )
            for job in exhausted_jobs:
                job.suggestion_input = {}
                job.result = None
                job.status = "failed"
                job.active_slot = None
                job.lease_owner = None
                job.lease_expires_at = None
                job.failure_code = "ai_suggestion_attempts_exhausted"
                job.completed_at = claim_time
                job.updated_at = claim_time

            jobs = list(
                session.scalars(
                    select(ClassroomPlanSuggestionJob)
                    .where(
                        ClassroomPlanSuggestionJob.run_at <= claim_time,
                        ClassroomPlanSuggestionJob.status.in_(("pending", "leased")),
                        ClassroomPlanSuggestionJob.attempts < self._max_attempts,
                        or_(
                            ClassroomPlanSuggestionJob.lease_expires_at.is_(None),
                            ClassroomPlanSuggestionJob.lease_expires_at <= claim_time,
                        ),
                    )
                    .order_by(
                        ClassroomPlanSuggestionJob.run_at,
                        ClassroomPlanSuggestionJob.id,
                    )
                    .limit(1)
                    .with_for_update(skip_locked=True)
                )
            )
            for job in jobs:
                job.status = "leased"
                job.lease_owner = worker_id
                job.lease_expires_at = claim_time + timedelta(seconds=self._LEASE_SECONDS)
                job.attempts += 1
                job.updated_at = claim_time
        return tuple(jobs)

    def run_due_jobs(self, worker_id: str) -> int:
        """Process due jobs. Provider errors become safe, pollable statuses."""

        jobs = self.claim_due_jobs(worker_id)
        for job in jobs:
            try:
                source = self._source_for_leased_job(job.id, worker_id)
                suggestion = self._suggestion_service.generate(source)
                self.complete(job.id, worker_id=worker_id, suggestion=suggestion)
            except UpstreamUnavailableError as error:
                retryable = error.retryable and self._suggestion_service.retry_provider_errors
                try:
                    self.record_failure(
                        job.id,
                        worker_id=worker_id,
                        failure_code=error.code,
                        retry_delay=self._retry_delay(job.attempts) if retryable else None,
                        occurred_at=self._utc_now(),
                    )
                except AuthorizationError:
                    continue
            except AuthorizationError:
                continue
        return len(jobs)

    def complete(
        self, job_id: str, *, worker_id: str, suggestion: PlanSuggestion
    ) -> None:
        """Persist only the strict display-safe output, then minimize source retention."""

        try:
            payload = PlanSuggestionPayload.model_validate(
                {
                    "title": suggestion.title,
                    "knowledge_points": [
                        point.model_dump(exclude_none=True)
                        for point in suggestion.knowledge_points
                    ],
                }
            ).model_dump(exclude_none=True, mode="json")
        except PydanticValidationError as error:
            raise UpstreamUnavailableError("ai_suggestion_response_invalid", retryable=False) from error

        now = self._utc_now()
        with self._session_factory.begin() as session:
            job = self._locked_job(session, job_id, worker_id)
            job.suggestion_input = {}
            job.result = payload
            job.status = "ready"
            job.active_slot = None
            job.lease_owner = None
            job.lease_expires_at = None
            job.failure_code = None
            job.completed_at = now
            job.updated_at = now

    def record_failure(
        self,
        job_id: str,
        *,
        worker_id: str,
        failure_code: str,
        retry_delay: timedelta | None,
        occurred_at: datetime | None = None,
    ) -> None:
        """Release a retryable lease, or retain only a stable client-safe failure code."""

        now = self._utc_now() if occurred_at is None else self._as_utc(occurred_at)
        with self._session_factory.begin() as session:
            job = self._locked_job(session, job_id, worker_id)
            job.failure_code = failure_code
            job.lease_owner = None
            job.lease_expires_at = None
            job.updated_at = now
            if retry_delay is not None:
                job.status = "pending"
                job.run_at = now + retry_delay
                return
            job.suggestion_input = {}
            job.result = None
            job.status = "failed"
            job.active_slot = None
            job.completed_at = now

    def _source_for_leased_job(self, job_id: str, worker_id: str) -> PlanSuggestionInput:
        with self._session_factory() as session:
            job = session.get(ClassroomPlanSuggestionJob, job_id)
            if job is None or job.status != "leased" or job.lease_owner != worker_id:
                raise UpstreamUnavailableError("ai_suggestion_job_lease_lost")
            try:
                return PlanSuggestionInput.model_validate(job.suggestion_input)
            except PydanticValidationError as error:
                raise UpstreamUnavailableError(
                    "ai_suggestion_input_invalid", retryable=False
                ) from error

    @staticmethod
    def _locked_job(
        session: Session, job_id: str, worker_id: str
    ) -> ClassroomPlanSuggestionJob:
        job = session.scalar(
            select(ClassroomPlanSuggestionJob)
            .where(ClassroomPlanSuggestionJob.id == job_id)
            .with_for_update()
        )
        if job is None:
            raise NotFoundError("plan_suggestion_job_not_found")
        if job.status != "leased" or job.lease_owner != worker_id:
            raise AuthorizationError("plan_suggestion_job_lease_not_owned")
        return job

    @staticmethod
    def _snapshot(job: ClassroomPlanSuggestionJob) -> PlanSuggestionJobSnapshot:
        if job.status == "ready":
            try:
                payload = PlanSuggestionPayload.model_validate(job.result)
            except PydanticValidationError as error:
                raise UpstreamUnavailableError(
                    "ai_suggestion_response_invalid", retryable=False
                ) from error
            return PlanSuggestionJobSnapshot(
                job_id=job.id,
                input_hash=job.request_hash,
                status="ready",
                failure_code=None,
                suggestion=PlanSuggestion(
                    title=payload.title,
                    knowledge_points=tuple(payload.knowledge_points),
                ),
            )
        if job.status == "failed":
            return PlanSuggestionJobSnapshot(
                job_id=job.id,
                input_hash=job.request_hash,
                status="failed",
                failure_code=job.failure_code or "ai_suggestion_upstream_unavailable",
                suggestion=None,
            )
        return PlanSuggestionJobSnapshot(
            job_id=job.id,
            input_hash=job.request_hash,
            status="pending",
            failure_code=None,
            suggestion=None,
        )

    def _retry_delay(self, attempts: int) -> timedelta | None:
        if attempts >= self._max_attempts:
            return None
        return self._RETRY_DELAYS[attempts - 1]

    @staticmethod
    def input_hash(suggestion_input: PlanSuggestionInput) -> str:
        value: dict[str, object] = {
            "profile_kind": suggestion_input.profile_kind,
            "title": suggestion_input.title,
            "statement": suggestion_input.statement,
            "material_bundle_hash": suggestion_input.material_bundle_hash,
        }
        canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _utc_now(self) -> datetime:
        return self._as_utc(self._clock())

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
