"""Idempotent student-assignment synchronization and acceptance."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime
from uuid import uuid4

from sqlalchemy.orm import Session, sessionmaker

from classroom_sync.auth.fincolab import StudentChildExperiment
from classroom_sync.errors import AuthorizationError, NotFoundError, RosterConflictError
from classroom_sync.models import AuditEvent, ExperimentPlanBinding, PlanVersion, StudentAssignment
from classroom_sync.repositories import ClassroomRepository


class AssignmentService:
    """Keep one natural-key assignment per verified student child environment."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        clock: Callable[[], datetime],
    ) -> None:
        self._session_factory = session_factory
        self._clock = clock

    def sync_assignments(
        self,
        plan_version: PlanVersion,
        roster: Iterable[StudentChildExperiment],
    ) -> tuple[StudentAssignment, ...]:
        roster_entries = tuple(roster)
        self._ensure_unique_roster(roster_entries)
        now = self._clock()
        with self._session_factory.begin() as session:
            repository = ClassroomRepository(session)
            binding = repository.get_binding(plan_version.space_id, plan_version.parent_algorithm_id)
            if binding is None:
                binding = ExperimentPlanBinding(
                    id=str(uuid4()),
                    space_id=plan_version.space_id,
                    parent_algorithm_id=plan_version.parent_algorithm_id,
                    plan_id=plan_version.plan_id,
                    plan_version=plan_version.version,
                    teacher_id=plan_version.teacher_id,
                    created_at=now,
                    updated_at=None,
                )
                session.add(binding)
                session.flush()
            else:
                binding.plan_id = plan_version.plan_id
                binding.plan_version = plan_version.version
                binding.teacher_id = plan_version.teacher_id
                binding.updated_at = now

            assignments: list[StudentAssignment] = []
            for entry in roster_entries:
                assignment = repository.get_assignment(
                    plan_id=plan_version.plan_id,
                    space_id=plan_version.space_id,
                    parent_algorithm_id=plan_version.parent_algorithm_id,
                    student_id=entry.student_id,
                    child_algorithm_id=entry.child_algorithm_id,
                )
                if assignment is None:
                    assignment = StudentAssignment(
                        id=str(uuid4()),
                        binding_id=binding.id,
                        space_id=plan_version.space_id,
                        parent_algorithm_id=plan_version.parent_algorithm_id,
                        child_algorithm_id=entry.child_algorithm_id,
                        workbench_id=entry.workbench_id,
                        student_id=entry.student_id,
                        plan_id=plan_version.plan_id,
                        plan_version=plan_version.version,
                        status="pending_acceptance",
                        scheduled_start_at=plan_version.scheduled_start_at,
                        scheduled_end_at=plan_version.scheduled_end_at,
                        accepted_at=None,
                        created_at=now,
                        updated_at=now,
                    )
                    session.add(assignment)
                elif assignment.status == "pending_acceptance":
                    assignment.binding_id = binding.id
                    assignment.workbench_id = entry.workbench_id
                    assignment.plan_id = plan_version.plan_id
                    assignment.plan_version = plan_version.version
                    assignment.scheduled_start_at = plan_version.scheduled_start_at
                    assignment.scheduled_end_at = plan_version.scheduled_end_at
                    assignment.updated_at = now
                assignments.append(assignment)

            session.add(
                AuditEvent(
                    id=str(uuid4()),
                    actor_id=plan_version.teacher_id,
                    event_type="student_assignments_synchronized",
                    entity_type="plan_version",
                    entity_id=plan_version.id,
                    request_id=None,
                    payload={"assignment_count": len(assignments)},
                    created_at=now,
                )
            )
        return tuple(assignments)

    def accept_assignment(self, assignment_id: str, *, student_id: str) -> StudentAssignment:
        now = self._clock()
        with self._session_factory.begin() as session:
            repository = ClassroomRepository(session)
            assignment = repository.get_assignment_by_id(assignment_id, for_update=True)
            if assignment is None:
                raise NotFoundError("student_assignment_not_found")
            if assignment.student_id != student_id:
                raise AuthorizationError("student_assignment_owner_mismatch")
            if assignment.status == "pending_acceptance":
                assignment.status = "ready"
                assignment.accepted_at = now
                assignment.updated_at = now
                session.add(
                    AuditEvent(
                        id=str(uuid4()),
                        actor_id=student_id,
                        event_type="student_assignment_accepted",
                        entity_type="student_assignment",
                        entity_id=assignment.id,
                        request_id=None,
                        payload={"plan_version": assignment.plan_version},
                        created_at=now,
                    )
                )
        return assignment

    def get_assignment(self, assignment_id: str) -> StudentAssignment:
        """Read an assignment before router-side student membership verification."""

        with self._session_factory() as session:
            assignment = ClassroomRepository(session).get_assignment_by_id(assignment_id)
            if assignment is None:
                raise NotFoundError("student_assignment_not_found")
            return assignment

    @staticmethod
    def _ensure_unique_roster(roster: tuple[StudentChildExperiment, ...]) -> None:
        natural_keys = {(entry.student_id, entry.child_algorithm_id) for entry in roster}
        if len(natural_keys) != len(roster):
            raise RosterConflictError("duplicate_student_child")
