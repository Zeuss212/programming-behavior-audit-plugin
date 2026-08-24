"""Revisioned student briefs and append-only teacher review overlays."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import cast
from uuid import uuid4

from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from classroom_sync.domain.schemas import ClassroomSchemaRegistry
from classroom_sync.errors import AuthorizationError, NotFoundError, ValidationError
from classroom_sync.models import (
    AuditEvent,
    ClassroomBriefAnalysisJob,
    EvidenceChunk,
    MonitorSession,
    PlanVersion,
    StudentAssignment,
    StudentBrief,
    TeacherReview,
)
from classroom_sync.services.brief_analysis import BriefAnalysisInput

EVIDENCE_REF_PATTERN = re.compile(r"^chunk-(\d+)#event-(\d+)$")


@dataclass(frozen=True)
class BriefContent:
    summary: str
    knowledge_points: tuple[dict[str, object], ...]
    process_overview: tuple[str, ...]
    issues: tuple[str, ...]


@dataclass(frozen=True)
class TeacherReviewInput:
    knowledge_point_reviews: tuple[dict[str, object], ...]
    comment: str


class BriefService:
    """Turn a session into one logical brief with auditable revision history."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        schema_registry: ClassroomSchemaRegistry,
        *,
        clock: Callable[[], datetime],
    ) -> None:
        self._session_factory = session_factory
        self._schema_registry = schema_registry
        self._clock = clock

    def submit(
        self,
        session_id: str,
        content: BriefContent,
        *,
        reason: str,
        request_ai_analysis: bool = False,
        analysis_input: Mapping[str, object] | None = None,
        analysis_available: bool = True,
    ) -> StudentBrief:
        """Persist a new revision while retaining the logical brief and first submit time."""

        now = self._utc_now()
        with self._session_factory.begin() as session:
            monitor_session = session.get(MonitorSession, session_id)
            if monitor_session is None:
                raise NotFoundError("monitor_session_not_found")
            assignment = session.get(StudentAssignment, monitor_session.assignment_id)
            if assignment is None:
                raise NotFoundError("student_assignment_not_found")
            plan_version = session.scalar(
                select(PlanVersion).where(
                    PlanVersion.plan_id == monitor_session.plan_id,
                    PlanVersion.version == monitor_session.plan_version,
                )
            )
            if plan_version is None:
                raise NotFoundError("plan_version_not_found")
            if request_ai_analysis != (analysis_input is not None):
                raise ValidationError("brief_analysis_consent_input_mismatch")
            try:
                private_analysis_input = (
                    BriefAnalysisInput.model_validate(analysis_input).model_dump(mode="json")
                    if analysis_input is not None
                    else None
                )
            except PydanticValidationError as error:
                raise ValidationError("brief_analysis_input_invalid") from error
            analysis_permitted = request_ai_analysis and plan_version.ai_policy == "allowed"
            previous = session.scalar(
                select(StudentBrief)
                .where(StudentBrief.session_id == session_id)
                .order_by(StudentBrief.revision.desc())
                .limit(1)
            )
            self._validate_evidence_refs(session, session_id, content.knowledge_points)

            revision = 1 if previous is None else previous.revision + 1
            previous_payload = previous.payload if previous is not None else None
            submitted_at = (
                previous_payload["submitted_at"] if previous_payload is not None else now.isoformat()
            )
            brief_id = previous_payload["brief_id"] if previous_payload is not None else str(uuid4())
            status = "completed" if monitor_session.completeness == "complete" else "partial"
            payload: dict[str, object] = {
                "schema_version": 1,
                "brief_id": brief_id,
                "session_id": session_id,
                "assignment_id": assignment.id,
                "plan_id": monitor_session.plan_id,
                "plan_version": monitor_session.plan_version,
                "revision": revision,
                "status": status,
                "data_completeness": monitor_session.completeness,
                "submission_reason": reason,
                "submitted_at": submitted_at,
                "evidence_cutoff_at": self._as_utc(monitor_session.evidence_cutoff_at).isoformat(),
                "active_duration_ms": self._active_duration_ms(monitor_session, now),
                "summary": content.summary,
                "knowledge_points": list(content.knowledge_points),
                "process_overview": list(content.process_overview),
                "issues": list(content.issues),
                "ai_analysis_status": (
                    "pending"
                    if analysis_permitted and analysis_available
                    else "unavailable"
                    if analysis_permitted
                    else "not_requested"
                ),
                "ai_analysis": None,
                "generated_at": now.isoformat(),
            }
            self._schema_registry.validate("student-brief", payload)
            brief = StudentBrief(
                id=str(uuid4()),
                session_id=session_id,
                assignment_id=assignment.id,
                revision=revision,
                status=status,
                data_completeness=monitor_session.completeness,
                submission_reason=reason,
                payload=payload,
                generated_at=now,
            )
            session.add(brief)
            if analysis_permitted and analysis_available:
                if private_analysis_input is None:
                    raise ValidationError("brief_analysis_input_required")
                session.flush()
                session.add(
                    ClassroomBriefAnalysisJob(
                        id=str(uuid4()),
                        source_brief_id=brief.id,
                        analysis_input=private_analysis_input,
                        run_at=now,
                        status="pending",
                        lease_owner=None,
                        lease_expires_at=None,
                        attempts=0,
                        failure_code=None,
                        completed_at=None,
                        created_at=now,
                        updated_at=now,
                    )
                )
            monitor_session.status = status
            monitor_session.submission_reason = reason
            monitor_session.active_slot = None
            monitor_session.updated_at = now
            assignment.status = "submitted"
            assignment.updated_at = now
            self._audit(session, assignment.student_id, "student_brief_submitted", brief.id, now)
        return brief

    def review(
        self,
        session_id: str,
        *,
        teacher_id: str,
        review_input: TeacherReviewInput,
    ) -> TeacherReview:
        """Store a teacher conclusion separately from the automatic student brief."""

        now = self._utc_now()
        with self._session_factory.begin() as session:
            monitor_session = session.get(MonitorSession, session_id)
            if monitor_session is None:
                raise NotFoundError("monitor_session_not_found")
            assignment = session.get(StudentAssignment, monitor_session.assignment_id)
            if assignment is None:
                raise NotFoundError("student_assignment_not_found")
            if assignment.plan_id != monitor_session.plan_id:
                raise AuthorizationError("session_assignment_plan_mismatch")
            payload: dict[str, object] = {
                "schema_version": 1,
                "review_id": str(uuid4()),
                "session_id": session_id,
                "teacher_id": teacher_id,
                "knowledge_point_reviews": list(review_input.knowledge_point_reviews),
                "comment": review_input.comment,
                "created_at": now.isoformat(),
            }
            self._schema_registry.validate("teacher-review", payload)
            review = TeacherReview(
                id=cast(str, payload["review_id"]),
                session_id=session_id,
                teacher_id=teacher_id,
                payload=payload,
                created_at=now,
            )
            session.add(review)
            self._audit(session, teacher_id, "teacher_review_submitted", review.id, now)
        return review

    def complete_analysis_job(
        self,
        job_id: str,
        *,
        worker_id: str,
        analysis: Mapping[str, object],
    ) -> StudentBrief:
        """Append one validated ready revision while atomically completing its lease."""

        now = self._utc_now()
        with self._session_factory.begin() as session:
            job = self._locked_analysis_job(session, job_id, worker_id)
            source = session.get(StudentBrief, job.source_brief_id)
            if source is None:
                raise NotFoundError("analysis_source_brief_not_found")
            brief = self._append_analysis_revision(
                session,
                source,
                ai_analysis_status="ready",
                ai_analysis=dict(analysis),
                generated_at=now,
            )
            job.status = "completed"
            job.lease_owner = None
            job.lease_expires_at = None
            job.failure_code = None
            job.completed_at = now
            job.updated_at = now
            self._audit(session, "classroom-ai-worker", "student_brief_ai_analysis_ready", brief.id, now)
        return brief

    def record_analysis_failure(
        self,
        job_id: str,
        *,
        worker_id: str,
        failure_code: str,
        retry_delay: timedelta | None,
        occurred_at: datetime | None = None,
    ) -> None:
        """Either reschedule a leased job or persist one safe unavailable result."""

        now = self._as_utc(occurred_at) if occurred_at is not None else self._utc_now()
        with self._session_factory.begin() as session:
            job = self._locked_analysis_job(session, job_id, worker_id)
            job.failure_code = failure_code
            job.lease_owner = None
            job.lease_expires_at = None
            job.updated_at = now
            if retry_delay is not None:
                job.status = "pending"
                job.run_at = now + retry_delay
                return

            source = session.get(StudentBrief, job.source_brief_id)
            if source is None:
                raise NotFoundError("analysis_source_brief_not_found")
            brief = self._append_analysis_revision(
                session,
                source,
                ai_analysis_status="unavailable",
                ai_analysis=None,
                generated_at=now,
            )
            job.status = "completed"
            job.completed_at = now
            self._audit(
                session,
                "classroom-ai-worker",
                "student_brief_ai_analysis_unavailable",
                brief.id,
                now,
            )

    def get_latest_brief(self, session_id: str) -> StudentBrief:
        """Return the latest revision of the one student-facing logical brief."""

        with self._session_factory() as session:
            brief = session.scalar(
                select(StudentBrief)
                .where(StudentBrief.session_id == session_id)
                .order_by(StudentBrief.revision.desc())
                .limit(1)
            )
            if brief is None:
                raise NotFoundError("student_brief_not_found")
            return brief

    def get_latest_teacher_review(self, session_id: str) -> TeacherReview | None:
        """Return the most recent teacher conclusion without mutating the brief."""

        with self._session_factory() as session:
            return session.scalar(
                select(TeacherReview)
                .where(TeacherReview.session_id == session_id)
                .order_by(TeacherReview.created_at.desc())
                .limit(1)
            )

    def get_assignment_for_session(self, session_id: str) -> StudentAssignment:
        """Read the assignment context required for teacher authorization."""

        with self._session_factory() as session:
            monitor_session = session.get(MonitorSession, session_id)
            if monitor_session is None:
                raise NotFoundError("monitor_session_not_found")
            assignment = session.get(StudentAssignment, monitor_session.assignment_id)
            if assignment is None:
                raise NotFoundError("student_assignment_not_found")
            return assignment

    def _append_analysis_revision(
        self,
        session: Session,
        source: StudentBrief,
        *,
        ai_analysis_status: str,
        ai_analysis: dict[str, object] | None,
        generated_at: datetime,
    ) -> StudentBrief:
        latest = session.scalar(
            select(StudentBrief)
            .where(StudentBrief.session_id == source.session_id)
            .order_by(StudentBrief.revision.desc())
            .limit(1)
        )
        if latest is None:
            raise NotFoundError("student_brief_not_found")
        payload = dict(source.payload)
        payload["revision"] = latest.revision + 1
        payload["ai_analysis_status"] = ai_analysis_status
        payload["ai_analysis"] = ai_analysis
        payload["generated_at"] = generated_at.isoformat()
        self._schema_registry.validate("student-brief", payload)
        brief = StudentBrief(
            id=str(uuid4()),
            session_id=source.session_id,
            assignment_id=source.assignment_id,
            revision=latest.revision + 1,
            status=source.status,
            data_completeness=source.data_completeness,
            submission_reason=source.submission_reason,
            payload=payload,
            generated_at=generated_at,
        )
        session.add(brief)
        return brief

    @staticmethod
    def _locked_analysis_job(
        session: Session, job_id: str, worker_id: str
    ) -> ClassroomBriefAnalysisJob:
        job = session.scalar(
            select(ClassroomBriefAnalysisJob)
            .where(ClassroomBriefAnalysisJob.id == job_id)
            .with_for_update()
        )
        if job is None:
            raise NotFoundError("brief_analysis_job_not_found")
        if job.status != "leased" or job.lease_owner != worker_id:
            raise AuthorizationError("brief_analysis_lease_not_owned")
        return job

    @staticmethod
    def _validate_evidence_refs(
        session: Session,
        session_id: str,
        knowledge_points: Iterable[dict[str, object]],
    ) -> None:
        chunks = {
            chunk.sequence: chunk
            for chunk in session.scalars(
                select(EvidenceChunk).where(EvidenceChunk.session_id == session_id)
            )
        }
        for knowledge_point in knowledge_points:
            evidence_refs = knowledge_point.get("evidence_refs")
            if not isinstance(evidence_refs, list):
                raise ValidationError("brief_evidence_reference_invalid")
            for evidence_ref in evidence_refs:
                if not isinstance(evidence_ref, str):
                    raise ValidationError("brief_evidence_reference_invalid")
                if evidence_ref == "session#missing-evidence":
                    continue
                match = EVIDENCE_REF_PATTERN.fullmatch(evidence_ref)
                if match is None:
                    raise ValidationError("brief_evidence_reference_invalid")
                chunk = chunks.get(int(match.group(1)))
                event_sequence = int(match.group(2))
                if chunk is None or not (chunk.first_event_sequence <= event_sequence <= chunk.last_event_sequence):
                    raise ValidationError("brief_evidence_reference_invalid")

    @staticmethod
    def _active_duration_ms(monitor_session: MonitorSession, now: datetime) -> int:
        last_activity = monitor_session.last_activity_at or monitor_session.created_at
        return max(0, int((min(BriefService._as_utc(last_activity), now) - BriefService._as_utc(monitor_session.created_at)).total_seconds() * 1000))

    def _utc_now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None:
            raise ValueError("Clock must return timezone-aware UTC datetimes.")
        return now.astimezone(timezone.utc)

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)

    @staticmethod
    def _audit(
        session: Session,
        actor_id: str,
        event_type: str,
        entity_id: str,
        created_at: datetime,
    ) -> None:
        session.add(
            AuditEvent(
                id=str(uuid4()),
                actor_id=actor_id,
                event_type=event_type,
                entity_type="classroom_brief",
                entity_id=entity_id,
                request_id=None,
                payload={},
                created_at=created_at,
            )
        )
