"""Allowlisted read models for the teacher and student classroom pages."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import datetime, timezone

from sqlalchemy.orm import Session, sessionmaker

from classroom_sync.errors import NotFoundError
from classroom_sync.models import (
    MonitorSession,
    PlanVersion,
    StudentAssignment,
    StudentBrief,
    TeacherReview,
)
from classroom_sync.repositories import ClassroomRepository


def monitoring_event_frame(snapshot: dict[str, object]) -> str:
    """Encode a credential-free classroom monitoring Server-Sent Event."""

    data = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return f"event: monitoring\ndata: {data}\n\n"


class ClassroomReadService:
    """Build page DTOs without returning tickets, raw logs, or object-storage metadata."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def get_experiment_plan(
        self, space_id: str, parent_algorithm_id: str
    ) -> dict[str, object]:
        """Return the published plan currently bound to one parent experiment."""

        with self._session_factory() as session:
            repository = ClassroomRepository(session)
            binding = repository.get_binding(space_id, parent_algorithm_id)
            if binding is None:
                plan_version = repository.get_latest_plan_version_for_scope(
                    space_id, parent_algorithm_id
                )
                if plan_version is None:
                    raise NotFoundError("experiment_plan_binding_not_found")
                return self._plan_summary(plan_version)
            plan_version = repository.get_plan_version(binding.plan_id, binding.plan_version)
            if plan_version is None:
                raise NotFoundError("plan_version_not_found")
            return self._plan_summary(plan_version)

    def list_student_assignments(self, student_id: str) -> list[dict[str, object]]:
        """Return assignments owned by one student, with no other student's records."""

        with self._session_factory() as session:
            repository = ClassroomRepository(session)
            return self._assignment_summaries(
                repository, repository.list_assignments_for_student(student_id)
            )

    def get_student_assignment(self, assignment_id: str) -> dict[str, object]:
        """Return one assignment after its router has checked membership and ownership."""

        with self._session_factory() as session:
            repository = ClassroomRepository(session)
            assignment = repository.get_assignment_by_id(assignment_id)
            if assignment is None:
                raise NotFoundError("student_assignment_not_found")
            return self._assignment_summaries(repository, [assignment])[0]

    def get_teacher_monitoring(self, plan_version_id: str) -> dict[str, object]:
        """Return lightweight student progress for a teacher-owned plan version."""

        with self._session_factory() as session:
            repository = ClassroomRepository(session)
            plan_version = repository.get_plan_version_by_id(plan_version_id)
            if plan_version is None:
                raise NotFoundError("plan_version_not_found")
            assignments = repository.list_assignments_for_plan_version(
                plan_version.plan_id, plan_version.version
            )
            latest_sessions = self._latest_sessions(
                repository.list_monitor_sessions_for_assignments(
                    [assignment.id for assignment in assignments]
                )
            )
            latest_briefs = self._latest_briefs(
                repository.list_student_briefs_for_sessions(
                    [monitor_session.id for monitor_session in latest_sessions.values()]
                )
            )
            latest_reviews = self._latest_reviews(
                repository.list_teacher_reviews_for_sessions(
                    [monitor_session.id for monitor_session in latest_sessions.values()]
                )
            )
            students: list[dict[str, object]] = []
            for assignment in assignments:
                monitor_session = latest_sessions.get(assignment.id)
                student_brief = (
                    latest_briefs.get(monitor_session.id) if monitor_session is not None else None
                )
                students.append(
                    {
                        "student_id": assignment.student_id,
                        "assignment_id": assignment.id,
                        "assignment_status": assignment.status,
                        "session": self._session_summary(monitor_session),
                        "brief": self._brief_summary(
                            student_brief,
                            latest_reviews.get(monitor_session.id)
                            if monitor_session is not None
                            else None,
                        ),
                    }
                )
            return {
                "plan_version_id": plan_version.id,
                "scheduled_end_at": self._isoformat(plan_version.scheduled_end_at),
                "students": students,
            }

    def _assignment_summaries(
        self,
        repository: ClassroomRepository,
        assignments: list[StudentAssignment],
    ) -> list[dict[str, object]]:
        latest_sessions = self._latest_sessions(
            repository.list_monitor_sessions_for_assignments(
                [assignment.id for assignment in assignments]
            )
        )
        plan_versions = {
            (plan_version.plan_id, plan_version.version): plan_version
            for plan_version in repository.list_plan_versions(
                [(assignment.plan_id, assignment.plan_version) for assignment in assignments]
            )
        }
        summaries: list[dict[str, object]] = []
        for assignment in assignments:
            plan_version = plan_versions.get((assignment.plan_id, assignment.plan_version))
            if plan_version is None:
                raise NotFoundError("plan_version_not_found")
            summaries.append(
                {
                    "assignment_id": assignment.id,
                    "space_id": assignment.space_id,
                    "parent_algorithm_id": assignment.parent_algorithm_id,
                    "child_algorithm_id": assignment.child_algorithm_id,
                    "workbench_id": assignment.workbench_id,
                    "plan_id": assignment.plan_id,
                    "plan_version": assignment.plan_version,
                    "title": self._title(plan_version),
                    "profile": dict(plan_version.profile),
                    "status": assignment.status,
                    "scheduled_start_at": self._isoformat(assignment.scheduled_start_at),
                    "scheduled_end_at": self._isoformat(assignment.scheduled_end_at),
                    "ai_policy": plan_version.ai_policy,
                    "session": self._session_summary(latest_sessions.get(assignment.id)),
                }
            )
        return summaries

    @staticmethod
    def _latest_sessions(records: list[MonitorSession]) -> dict[str, MonitorSession]:
        latest: dict[str, MonitorSession] = {}
        for record in records:
            latest.setdefault(record.assignment_id, record)
        return latest

    @staticmethod
    def _latest_briefs(records: list[StudentBrief]) -> dict[str, StudentBrief]:
        latest: dict[str, StudentBrief] = {}
        for record in records:
            latest.setdefault(record.session_id, record)
        return latest

    @staticmethod
    def _latest_reviews(records: list[TeacherReview]) -> dict[str, TeacherReview]:
        latest: dict[str, TeacherReview] = {}
        for record in records:
            latest.setdefault(record.session_id, record)
        return latest

    @staticmethod
    def _title(plan_version: PlanVersion) -> str:
        title = plan_version.profile.get("title")
        return title if isinstance(title, str) else ""

    @classmethod
    def _plan_summary(cls, plan_version: PlanVersion) -> dict[str, object]:
        response: dict[str, object] = {
            "plan_version_id": plan_version.id,
            "plan_id": plan_version.plan_id,
            "version": plan_version.version,
            "title": cls._title(plan_version),
            "profile": dict(plan_version.profile),
            "scheduled_start_at": cls._isoformat(plan_version.scheduled_start_at),
            "scheduled_end_at": cls._isoformat(plan_version.scheduled_end_at),
            "ai_policy": plan_version.ai_policy,
            "published_at": cls._isoformat(plan_version.published_at),
        }
        if plan_version.assessment_config is not None:
            response["content_schema_version"] = plan_version.content_schema_version
            response["assessment_config"] = dict(plan_version.assessment_config)
        return response

    @classmethod
    def _session_summary(cls, monitor_session: MonitorSession | None) -> dict[str, object] | None:
        if monitor_session is None:
            return None
        return {
            "id": monitor_session.id,
            "status": monitor_session.status,
            "last_activity_at": cls._isoformat(monitor_session.last_activity_at),
            "submission_reason": monitor_session.submission_reason,
        }

    @staticmethod
    def _brief_summary(
        student_brief: StudentBrief | None,
        teacher_review: TeacherReview | None,
    ) -> dict[str, object] | None:
        if student_brief is None:
            return None
        ai_analysis_status = student_brief.payload.get("ai_analysis_status")
        response: dict[str, object] = {
            "status": student_brief.status,
            "revision": student_brief.revision,
            "ai_analysis_status": (
                ai_analysis_status
                if ai_analysis_status in {"not_requested", "pending", "ready", "unavailable"}
                else "not_requested"
            ),
            "mastery_overview": ClassroomReadService._mastery_overview(
                student_brief, teacher_review
            ),
        }
        assessment_score = ClassroomReadService._assessment_score_summary(student_brief)
        if assessment_score is not None:
            response["assessment_score"] = assessment_score
        return response

    @staticmethod
    def _assessment_score_summary(
        student_brief: StudentBrief,
    ) -> dict[str, object] | None:
        raw_score = student_brief.payload.get("assessment_score")
        if not isinstance(raw_score, Mapping):
            return None
        overall_score = raw_score.get("overall_score")
        scoring_rule_version = raw_score.get("scoring_rule_version")
        if (
            not isinstance(overall_score, (int, float))
            or isinstance(overall_score, bool)
            or not 0 <= float(overall_score) <= 100
            or scoring_rule_version != "ai-score-v1"
        ):
            return None
        return {
            "overall_score": float(overall_score),
            "scoring_rule_version": scoring_rule_version,
        }

    @staticmethod
    def _mastery_overview(
        student_brief: StudentBrief,
        teacher_review: TeacherReview | None,
    ) -> dict[str, object]:
        raw_points = student_brief.payload.get("knowledge_points")
        points = raw_points if isinstance(raw_points, list) else []
        base_statuses = {
            "mastered",
            "partial",
            "not_mastered",
            "not_demonstrated",
            "review_required",
        }
        normalized_rows: list[dict[str, object]] = []
        for point in points:
            if not isinstance(point, Mapping):
                continue
            point_id = point.get("knowledge_point_id")
            name = point.get("name")
            status = point.get("status")
            if (
                not isinstance(point_id, str)
                or not isinstance(name, str)
                or status not in base_statuses
            ):
                continue
            gap = point.get("gap")
            references = point.get("evidence_refs")
            normalized_rows.append(
                {
                    "knowledge_point_id": point_id,
                    "name": name,
                    "status": status,
                    "reason": gap if isinstance(gap, str) else "需要查看相关过程证据。",
                    "evidence_refs": references if isinstance(references, list) else [],
                }
            )

        review_by_point: dict[str, tuple[str, str]] = {}
        if teacher_review is not None:
            raw_reviews = teacher_review.payload.get("knowledge_point_reviews")
            if isinstance(raw_reviews, list):
                for review in raw_reviews:
                    if not isinstance(review, Mapping):
                        continue
                    point_id = review.get("knowledge_point_id")
                    status = review.get("status")
                    reason = review.get("reason")
                    if (
                        isinstance(point_id, str)
                        and status in base_statuses
                        and isinstance(reason, str)
                    ):
                        review_by_point[point_id] = (status, reason)

        applied_teacher_review = False
        counts = {
            "mastered": 0,
            "partial": 0,
            "not_mastered": 0,
            "evidence_insufficient": 0,
            "review_required": 0,
        }
        attention: list[tuple[int, int, dict[str, object]]] = []
        priority = {
            "not_mastered": 0,
            "partial": 1,
            "review_required": 2,
            "evidence_insufficient": 3,
        }
        for index, row in enumerate(normalized_rows):
            point_id = str(row["knowledge_point_id"])
            override = review_by_point.get(point_id)
            if override is not None:
                row["status"], row["reason"] = override
                applied_teacher_review = True
            display_status = (
                "evidence_insufficient"
                if row["status"] == "not_demonstrated"
                else str(row["status"])
            )
            counts[display_status] += 1
            if display_status == "mastered":
                continue
            raw_references = row.get("evidence_refs")
            evidence_count = (
                len(
                    [
                        reference
                        for reference in raw_references
                        if isinstance(reference, str)
                        and re.fullmatch(
                            r"chunk-[1-9][0-9]*#event-[1-9][0-9]*", reference
                        )
                    ]
                )
                if isinstance(raw_references, list)
                else 0
            )
            attention.append(
                (
                    priority[display_status],
                    index,
                    {
                        "knowledge_point_id": point_id,
                        "name": str(row["name"])[:200],
                        "status": display_status,
                        "reason": str(row["reason"])[:500],
                        "evidence_count": evidence_count,
                    },
                )
            )

        attention.sort(key=lambda item: (item[0], item[1]))
        return {
            "counts": counts,
            "attention_items": [item[2] for item in attention[:3]],
            "data_completeness": student_brief.data_completeness,
            "source": "teacher" if applied_teacher_review else "automatic",
        }

    @staticmethod
    def _isoformat(value: datetime | None) -> str | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
