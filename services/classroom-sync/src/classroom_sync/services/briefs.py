"""Revisioned student briefs and append-only teacher review overlays."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import cast
from uuid import UUID, uuid4

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
_EVENT_DESCRIPTIONS = {
    "edit": {
        "编辑了代码。",
        "删除或替换了代码。",
        "粘贴并编辑了代码。",
    },
    "run": {"执行了一次代码。"},
    "run_failure": {"运行出现异常，之后可结合后续编辑与重运行判断修正过程。"},
    "run_success": {"完成一次无异常运行；这不代表答案一定正确。"},
}


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
        submission_id: str | None = None,
        request_ai_analysis: bool = False,
        analysis_input: Mapping[str, object] | None = None,
        analysis_available: bool = True,
    ) -> StudentBrief:
        """Persist a new revision while retaining the logical brief and first submit time."""

        now = self._utc_now()
        if submission_id is not None:
            try:
                parsed_submission_id = UUID(submission_id)
            except ValueError as error:
                raise ValidationError("brief_submission_id_invalid") from error
            if str(parsed_submission_id) != submission_id:
                raise ValidationError("brief_submission_id_invalid")
        submission_hash = self._submission_hash(
            content,
            reason=reason,
            request_ai_analysis=request_ai_analysis,
            analysis_input=analysis_input,
        )
        with self._session_factory.begin() as session:
            monitor_session = session.scalar(
                select(MonitorSession)
                .where(MonitorSession.id == session_id)
                .with_for_update()
            )
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
            if submission_id is not None:
                replay = session.scalar(
                    select(StudentBrief).where(
                        StudentBrief.session_id == session_id,
                        StudentBrief.submission_id == submission_id,
                    )
                )
                if replay is not None:
                    if replay.submission_hash != submission_hash:
                        raise ValidationError("brief_submission_id_conflict")
                    return replay
            if request_ai_analysis != (analysis_input is not None):
                raise ValidationError("brief_analysis_consent_input_mismatch")
            try:
                private_source = (
                    BriefAnalysisInput.model_validate(analysis_input)
                    if analysis_input is not None
                    else None
                )
            except PydanticValidationError as error:
                raise ValidationError("brief_analysis_input_invalid") from error
            analysis_context_available = True
            if private_source is not None:
                analysis_context_available = self._validate_analysis_input_context(
                    session,
                    session_id,
                    plan_version,
                    private_source,
                )
            private_analysis_input = (
                private_source.model_dump(mode="json") if private_source is not None else None
            )
            analysis_permitted = request_ai_analysis and plan_version.ai_policy == "allowed"
            analysis_ready = analysis_available and analysis_context_available
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
                    if analysis_permitted and analysis_ready
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
                submission_id=submission_id,
                submission_hash=submission_hash if submission_id is not None else None,
                status=status,
                data_completeness=monitor_session.completeness,
                submission_reason=reason,
                payload=payload,
                generated_at=now,
            )
            session.add(brief)
            if analysis_permitted and analysis_ready:
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
            job.analysis_input = {}
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
            job.analysis_input = {}
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
            submission_id=None,
            submission_hash=None,
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
                if chunk is None or not (
                    chunk.first_event_sequence
                    <= event_sequence
                    <= chunk.last_event_sequence
                ):
                    raise ValidationError("brief_evidence_reference_invalid")

    @staticmethod
    def _validate_analysis_input_context(
        session: Session,
        session_id: str,
        plan_version: PlanVersion,
        source: BriefAnalysisInput,
    ) -> bool:
        profile = plan_version.profile
        if source.lesson["title"] != BriefService._plan_text(profile.get("title"), 200):
            raise ValidationError("brief_analysis_input_context_invalid")
        raw_points = profile.get("knowledge_points")
        raw_dimensions = profile.get("dimensions")
        dimensions = {
            item.get("knowledge_point_id"): item
            for item in raw_dimensions
            if isinstance(item, Mapping)
            and isinstance(item.get("knowledge_point_id"), str)
        } if isinstance(raw_dimensions, list) else {}
        expected_points: list[dict[str, object]] = []
        if isinstance(raw_points, list):
            for point in raw_points[:10]:
                if not isinstance(point, Mapping) or not isinstance(point.get("id"), str):
                    continue
                point_id = cast(str, point["id"])
                dimension = dimensions.get(point_id)
                raw_criteria = (
                    dimension.get("evidence_criteria")
                    if isinstance(dimension, Mapping)
                    else None
                )
                criteria = [
                    {
                        "id": criterion.get("id"),
                        "direction": criterion.get("direction"),
                        "statement": BriefService._plan_text(
                            criterion.get("statement"), 300
                        ),
                    }
                    for criterion in raw_criteria[:10]
                    if isinstance(criterion, Mapping)
                    and isinstance(criterion.get("id"), str)
                    and criterion.get("direction") in {"support", "exclude"}
                    and BriefService._plan_text(criterion.get("statement"), 300)
                ] if isinstance(raw_criteria, list) else []
                expected_points.append(
                    {
                        "knowledge_point_id": point_id,
                        "name": BriefService._plan_text(point.get("name"), 80),
                        "description": BriefService._plan_text(
                            point.get("description"), 500
                        ),
                        "question": BriefService._plan_text(
                            dimension.get("question")
                            if isinstance(dimension, Mapping)
                            else "",
                            200,
                        ),
                        "evidence_criteria": criteria,
                    }
                )
        actual_points = [point.model_dump(mode="json") for point in source.knowledge_points]
        if actual_points != expected_points:
            raise ValidationError("brief_analysis_input_context_invalid")

        chunks = {
            chunk.sequence: chunk
            for chunk in session.scalars(
                select(EvidenceChunk).where(EvidenceChunk.session_id == session_id)
            )
        }
        context_available = True
        trusted_by_event_id: dict[str, Mapping[str, object]] = {}
        for event in source.evidence_events:
            if event.description not in _EVENT_DESCRIPTIONS[event.kind]:
                raise ValidationError("brief_analysis_input_context_invalid")
            match = EVIDENCE_REF_PATTERN.fullmatch(event.event_id)
            if match is None:
                raise ValidationError("brief_analysis_input_context_invalid")
            chunk = chunks.get(int(match.group(1)))
            event_sequence = int(match.group(2))
            if (
                event.sequence != event_sequence
                or chunk is None
                or not (
                    chunk.first_event_sequence
                    <= event_sequence
                    <= chunk.last_event_sequence
                )
            ):
                raise ValidationError("brief_analysis_input_context_invalid")
            raw_manifest = chunk.analysis_manifest
            trusted_events = (
                raw_manifest.get("events") if isinstance(raw_manifest, Mapping) else None
            )
            trusted_event = (
                trusted_events.get(str(event_sequence))
                if isinstance(trusted_events, Mapping)
                else None
            )
            if trusted_event is None:
                context_available = False
            elif not isinstance(trusted_event, Mapping) or (
                trusted_event.get("kind") != event.kind
                or trusted_event.get("description") != event.description
            ):
                raise ValidationError("brief_analysis_input_context_invalid")
            else:
                trusted_by_event_id[event.event_id] = trusted_event
        for snapshot in source.code_snapshots:
            trusted_event = trusted_by_event_id.get(snapshot.event_id)
            expected_hash = (
                trusted_event.get("source_sha256")
                if isinstance(trusted_event, Mapping)
                else None
            )
            if expected_hash != hashlib.sha256(snapshot.source.encode("utf-8")).hexdigest():
                context_available = False
        return context_available

    @staticmethod
    def _plan_text(value: object, limit: int) -> str:
        return value.strip()[:limit] if isinstance(value, str) else ""

    @staticmethod
    def _submission_hash(
        content: BriefContent,
        *,
        reason: str,
        request_ai_analysis: bool,
        analysis_input: Mapping[str, object] | None,
    ) -> str:
        canonical = json.dumps(
            {
                "content": asdict(content),
                "reason": reason,
                "request_ai_analysis": request_ai_analysis,
                "analysis_input": analysis_input,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

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
