"""Small SQLAlchemy repository for transactional classroom workflow operations."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from classroom_sync.models import (
    AuditEvent,
    ExperimentPlanBinding,
    PlanDraft,
    PlanVersion,
    StudentAssignment,
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
        space_id: str,
        parent_algorithm_id: str,
        student_id: str,
        child_algorithm_id: str,
    ) -> StudentAssignment | None:
        return self.session.scalar(
            select(StudentAssignment).where(
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

    def add_audit_event(self, event: AuditEvent) -> None:
        self.session.add(event)
