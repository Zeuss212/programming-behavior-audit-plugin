"""Small SQLAlchemy repository for transactional classroom workflow operations."""

from __future__ import annotations

from sqlalchemy import select, tuple_
from sqlalchemy.orm import Session

from classroom_sync.models import (
    AuditEvent,
    ExperimentPlanBinding,
    MonitorSession,
    PlanDraft,
    PlanVersion,
    StudentAssignment,
    StudentBrief,
)


class ClassroomRepository:
    """Centralize locking reads and natural-key lookups used by domain services."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get_plan_draft(self, draft_id: str, *, for_update: bool = False) -> PlanDraft | None:
        statement = select(PlanDraft).where(PlanDraft.id == draft_id)
        if for_update:
            statement = statement.with_for_update()
        return self.session.scalar(statement)

    def latest_plan_version(self, plan_id: str) -> PlanVersion | None:
        statement = (
            select(PlanVersion)
            .where(PlanVersion.plan_id == plan_id)
            .order_by(PlanVersion.version.desc())
            .limit(1)
        )
        return self.session.scalar(statement)

    def get_plan_version(self, plan_id: str, version: int) -> PlanVersion | None:
        return self.session.scalar(
            select(PlanVersion).where(
                PlanVersion.plan_id == plan_id,
                PlanVersion.version == version,
            )
        )

    def get_plan_version_by_id(self, plan_version_id: str) -> PlanVersion | None:
        return self.session.get(PlanVersion, plan_version_id)

    def list_plan_versions(self, plan_keys: list[tuple[str, int]]) -> list[PlanVersion]:
        if not plan_keys:
            return []
        return list(
            self.session.scalars(
                select(PlanVersion).where(
                    tuple_(PlanVersion.plan_id, PlanVersion.version).in_(plan_keys)
                )
            )
        )

    def get_binding(self, space_id: str, parent_algorithm_id: str) -> ExperimentPlanBinding | None:
        return self.session.scalar(
            select(ExperimentPlanBinding).where(
                ExperimentPlanBinding.space_id == space_id,
                ExperimentPlanBinding.parent_algorithm_id == parent_algorithm_id,
            )
        )

    def get_assignment(
        self,
        *,
        plan_id: str,
        space_id: str,
        parent_algorithm_id: str,
        student_id: str,
        child_algorithm_id: str,
    ) -> StudentAssignment | None:
        return self.session.scalar(
            select(StudentAssignment).where(
                StudentAssignment.plan_id == plan_id,
                StudentAssignment.space_id == space_id,
                StudentAssignment.parent_algorithm_id == parent_algorithm_id,
                StudentAssignment.student_id == student_id,
                StudentAssignment.child_algorithm_id == child_algorithm_id,
            )
        )

    def get_assignment_by_id(self, assignment_id: str, *, for_update: bool = False) -> StudentAssignment | None:
        statement = select(StudentAssignment).where(StudentAssignment.id == assignment_id)
        if for_update:
            statement = statement.with_for_update()
        return self.session.scalar(statement)

    def list_assignments_for_student(self, student_id: str) -> list[StudentAssignment]:
        return list(
            self.session.scalars(
                select(StudentAssignment)
                .where(StudentAssignment.student_id == student_id)
                .order_by(StudentAssignment.scheduled_start_at.desc(), StudentAssignment.id)
            )
        )

    def list_assignments_for_plan_version(
        self, plan_id: str, plan_version: int
    ) -> list[StudentAssignment]:
        return list(
            self.session.scalars(
                select(StudentAssignment)
                .where(
                    StudentAssignment.plan_id == plan_id,
                    StudentAssignment.plan_version == plan_version,
                )
                .order_by(StudentAssignment.student_id, StudentAssignment.id)
            )
        )

    def list_monitor_sessions_for_assignments(
        self, assignment_ids: list[str]
    ) -> list[MonitorSession]:
        if not assignment_ids:
            return []
        return list(
            self.session.scalars(
                select(MonitorSession)
                .where(MonitorSession.assignment_id.in_(assignment_ids))
                .order_by(MonitorSession.assignment_id, MonitorSession.created_at.desc())
            )
        )

    def list_student_briefs_for_sessions(self, session_ids: list[str]) -> list[StudentBrief]:
        if not session_ids:
            return []
        return list(
            self.session.scalars(
                select(StudentBrief)
                .where(StudentBrief.session_id.in_(session_ids))
                .order_by(StudentBrief.session_id, StudentBrief.revision.desc())
            )
        )

    def add_audit_event(self, event: AuditEvent) -> None:
        self.session.add(event)
