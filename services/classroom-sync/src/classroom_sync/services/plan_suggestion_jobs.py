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
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from classroom_sync.errors import AuthorizationError, NotFoundError, UpstreamUnavailableError
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
        teacher_id: str,
        space_id: str,
        parent_algorithm_id: str,
        suggestion_input: PlanSuggestionInput,
    ) -> PlanSuggestionJobSnapshot:
        """Create one active job, or reuse an identical active request safely."""

        now = self._utc_now()
        source = suggestion_input.model_dump(mode="json")
        request_hash = self._request_hash(source)
        with self._session_factory.begin() as session:
            existing = session.scalar(
                select(ClassroomPlanSuggestionJob)
                .where(
                    ClassroomPlanSuggestionJob.teacher_id == teacher_id,
                    ClassroomPlanSuggestionJob.space_id == space_id,
                    ClassroomPlanSuggestionJob.parent_algorithm_id == parent_algorithm_id,
                    ClassroomPlanSuggestionJob.request_hash == request_hash,
                    ClassroomPlanSuggestionJob.active_slot == 1,
                )
                .with_for_update()
            )
            if existing is not None:
                return self._snapshot(existing)

            job = ClassroomPlanSuggestionJob(
                id=str(uuid4()),
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

    def claim_due_jobs(
        self, worker_id: str, now: datetime | None = None
    ) -> tuple[ClassroomPlanSuggestionJob, ...]:
        claim_time = self._utc_now() if now is None else self._as_utc(now)
        with self._session_factory.begin() as session:
            jobs = list(
                session.scalars(
                    select(ClassroomPlanSuggestionJob)
                    .where(
                        ClassroomPlanSuggestionJob.run_at <= claim_time,
                        ClassroomPlanSuggestionJob.status.in_(("pending", "leased")),
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
                status="failed",
                failure_code=job.failure_code or "ai_suggestion_upstream_unavailable",
                suggestion=None,
            )
        return PlanSuggestionJobSnapshot(
            job_id=job.id,
            status="pending",
            failure_code=None,
            suggestion=None,
        )

    def _retry_delay(self, attempts: int) -> timedelta | None:
        if attempts >= self._max_attempts:
            return None
        return self._RETRY_DELAYS[attempts - 1]

    @staticmethod
    def _request_hash(value: dict[str, object]) -> str:
        canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _utc_now(self) -> datetime:
        return self._as_utc(self._clock())

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
