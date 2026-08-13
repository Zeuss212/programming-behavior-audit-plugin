"""Allowlisted read models for the teacher and student classroom pages."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy.orm import Session, sessionmaker

from classroom_sync.errors import NotFoundError
from classroom_sync.models import MonitorSession, PlanVersion, StudentAssignment, StudentBrief
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
                raise NotFoundError("experiment_plan_binding_not_found")
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
                        "brief": self._brief_summary(student_brief),
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
    def _title(plan_version: PlanVersion) -> str:
        title = plan_version.profile.get("title")
        return title if isinstance(title, str) else ""

    @classmethod
    def _plan_summary(cls, plan_version: PlanVersion) -> dict[str, object]:
        return {
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
    def _brief_summary(student_brief: StudentBrief | None) -> dict[str, object] | None:
        if student_brief is None:
            return None
        return {"status": student_brief.status, "revision": student_brief.revision}

    @staticmethod
    def _isoformat(value: datetime | None) -> str | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
