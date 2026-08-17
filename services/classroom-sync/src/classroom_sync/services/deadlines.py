"""Persistent lease-based deadline jobs that survive API and worker restarts."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from classroom_sync.errors import AuthorizationError, ConflictError, NotFoundError, ValidationError
from classroom_sync.models import (
    ClassroomDeadlineJob,
    EvidenceChunk,
    MonitorSession,
    PlanVersion,
    StudentBrief,
)
from classroom_sync.services.briefs import BriefContent, BriefService

DEFAULT_LEASE_SECONDS = 60


class DeadlineService:
    """Schedule a hard cutoff, claim due jobs with leases, and close sessions idempotently."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        brief_service: BriefService,
        *,
        clock: Callable[[], datetime],
    ) -> None:
        self._session_factory = session_factory
        self._brief_service = brief_service
        self._clock = clock

    def schedule_session_deadline(self, session_id: str) -> ClassroomDeadlineJob:
        """Create or reconcile the one durable deadline job for a monitor session."""

        now = self._utc_now()
        with self._session_factory.begin() as session:
            monitor_session = session.get(MonitorSession, session_id)
            if monitor_session is None:
                raise NotFoundError("monitor_session_not_found")
            job = session.scalar(
                select(ClassroomDeadlineJob)
                .where(ClassroomDeadlineJob.session_id == session_id)
                .with_for_update()
            )
            if job is None:
                job = ClassroomDeadlineJob(
                    id=str(uuid4()),
                    session_id=session_id,
                    run_at=self._as_utc(monitor_session.evidence_cutoff_at),
                    status="pending",
                    lease_owner=None,
                    lease_expires_at=None,
                    attempts=0,
                    completed_at=None,
                    created_at=now,
                    updated_at=now,
                )
                session.add(job)
            elif job.status != "completed":
                job.run_at = self._as_utc(monitor_session.evidence_cutoff_at)
                job.status = "pending"
                job.lease_owner = None
                job.lease_expires_at = None
                job.updated_at = now
        return job

    def record_teacher_end(self, session_id: str, actual_end_at: datetime) -> MonitorSession:
        """Move the cutoff earlier only; teachers cannot extend a published class window."""

        now = self._utc_now()
        actual_end = self._as_utc(actual_end_at)
        with self._session_factory.begin() as session:
            monitor_session = session.get(MonitorSession, session_id)
            if monitor_session is None:
                raise NotFoundError("monitor_session_not_found")
            scheduled_end = self._as_utc(monitor_session.scheduled_end_at)
            if actual_end > scheduled_end:
                raise ValidationError("teacher_end_after_schedule")
            current_actual_end = self._as_utc(monitor_session.actual_end_at or scheduled_end)
            if actual_end > current_actual_end:
                raise ValidationError("teacher_end_cannot_extend")
            monitor_session.actual_end_at = actual_end
            monitor_session.evidence_cutoff_at = actual_end + timedelta(minutes=15)
            monitor_session.updated_at = now
            job = session.scalar(
                select(ClassroomDeadlineJob)
                .where(ClassroomDeadlineJob.session_id == session_id)
                .with_for_update()
            )
            if job is None:
                job = ClassroomDeadlineJob(
                    id=str(uuid4()),
                    session_id=session_id,
                    run_at=monitor_session.evidence_cutoff_at,
                    status="pending",
                    lease_owner=None,
                    lease_expires_at=None,
                    attempts=0,
                    completed_at=None,
                    created_at=now,
                    updated_at=now,
                )
                session.add(job)
            elif job.status != "completed":
                job.run_at = monitor_session.evidence_cutoff_at
                job.status = "pending"
                job.lease_owner = None
                job.lease_expires_at = None
                job.updated_at = now
        return monitor_session

    def claim_due_jobs(
        self,
        worker_id: str,
        now: datetime | None = None,
        *,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
    ) -> tuple[ClassroomDeadlineJob, ...]:
        """Lease each due job once; expired leases are safe for a replacement worker to reclaim."""

        claim_time = self._utc_now() if now is None else self._as_utc(now)
        with self._session_factory.begin() as session:
            jobs = list(
                session.scalars(
                    select(ClassroomDeadlineJob)
                    .where(
                        ClassroomDeadlineJob.run_at <= claim_time,
                        ClassroomDeadlineJob.status != "completed",
                        or_(
                            ClassroomDeadlineJob.lease_expires_at.is_(None),
                            ClassroomDeadlineJob.lease_expires_at <= claim_time,
                        ),
                    )
                    .order_by(ClassroomDeadlineJob.run_at, ClassroomDeadlineJob.id)
                    .with_for_update(skip_locked=True)
                )
            )
            for job in jobs:
                job.status = "leased"
                job.lease_owner = worker_id
                job.lease_expires_at = claim_time + timedelta(seconds=lease_seconds)
                job.attempts += 1
                job.updated_at = claim_time
        return tuple(jobs)

    def close_session(self, session_id: str, *, worker_id: str) -> StudentBrief:
        """Produce the existing brief or one deterministic partial/completed deadline brief."""

        now = self._utc_now()
        with self._session_factory.begin() as session:
            job = session.scalar(
                select(ClassroomDeadlineJob)
                .where(ClassroomDeadlineJob.session_id == session_id)
                .with_for_update()
            )
            if job is None:
                raise NotFoundError("deadline_job_not_found")
            if job.status == "completed":
                existing = self._latest_brief(session, session_id)
                if existing is None:
                    raise ConflictError("deadline_completed_without_brief")
                return existing
            if job.lease_owner != worker_id:
                raise AuthorizationError("deadline_lease_not_owned")
            monitor_session = session.get(MonitorSession, session_id)
            if monitor_session is None:
                raise NotFoundError("monitor_session_not_found")
            existing = self._latest_brief(session, session_id)
            if existing is not None:
                job.status = "completed"
                job.completed_at = now
                job.lease_owner = None
                job.lease_expires_at = None
                job.updated_at = now
                return existing
            evidence_chunks = list(
                session.scalars(
                    select(EvidenceChunk)
                    .where(EvidenceChunk.session_id == session_id)
                    .order_by(EvidenceChunk.sequence)
                )
            )
            plan_version = session.scalar(
                select(PlanVersion).where(
                    PlanVersion.plan_id == monitor_session.plan_id,
                    PlanVersion.version == monitor_session.plan_version,
                )
            )
            if plan_version is None:
                raise NotFoundError("plan_version_not_found")
            if not evidence_chunks:
                monitor_session.completeness = "partial"
                monitor_session.missing_ranges = [{"from": 1, "to": 1}]
            monitor_session.updated_at = now
            content = self._deadline_content(plan_version, evidence_chunks)

        brief = self._brief_service.submit(session_id, content, reason="system_deadline")
        with self._session_factory.begin() as session:
            job = session.scalar(
                select(ClassroomDeadlineJob)
                .where(ClassroomDeadlineJob.session_id == session_id)
                .with_for_update()
            )
            if job is None:
                raise NotFoundError("deadline_job_not_found")
            job.status = "completed"
            job.completed_at = now
            job.lease_owner = None
            job.lease_expires_at = None
            job.updated_at = now
        return brief

    @staticmethod
    def _latest_brief(session: Session, session_id: str) -> StudentBrief | None:
        return session.scalar(
            select(StudentBrief)
            .where(StudentBrief.session_id == session_id)
            .order_by(StudentBrief.revision.desc())
            .limit(1)
        )

    @staticmethod
    def _deadline_content(
        plan_version: PlanVersion,
        evidence_chunks: list[EvidenceChunk],
    ) -> BriefContent:
        raw_knowledge_points = plan_version.profile.get("knowledge_points")
        knowledge_points = raw_knowledge_points if isinstance(raw_knowledge_points, list) else []
        if not knowledge_points:
            knowledge_points = [{"id": "plan_requirements", "name": "课堂要求"}]
        if evidence_chunks:
            first_chunk = evidence_chunks[0]
            evidence_ref = f"chunk-{first_chunk.sequence}#event-{first_chunk.first_event_sequence}"
            status = "partial"
            demonstrated = "截止前检测到部分监控证据。"
            gap = "证据尚不足以确认全部课堂目标。"
        else:
            evidence_ref = "session#missing-evidence"
            status = "not_demonstrated"
            demonstrated = "截止时没有可用的监控证据。"
            gap = "无法确认学生是否完成课堂目标。"
        points: list[dict[str, object]] = []
        for index, raw_point in enumerate(knowledge_points, start=1):
            point = raw_point if isinstance(raw_point, dict) else {}
            point_id = point.get("id")
            name = point.get("name")
            points.append(
                {
                    "knowledge_point_id": point_id if isinstance(point_id, str) else f"KP_{index}",
                    "name": name if isinstance(name, str) else f"知识点 {index}",
                    "status": status,
                    "evidence_refs": [evidence_ref],
                    "demonstrated": demonstrated,
                    "gap": gap,
                    "teacher_suggestion": "请结合课堂交流与原始日志进行复核。",
                }
            )
        return BriefContent(
            summary="系统在课后 15 分钟截止时自动收口本次课堂监控。",
            knowledge_points=tuple(points),
            process_overview=("系统自动收口，未收到学生手动提交。",),
            issues=("请优先查看数据完整度和证据引用。",),
        )

    def _utc_now(self) -> datetime:
        return self._as_utc(self._clock())

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
