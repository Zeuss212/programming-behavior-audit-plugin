"""Explicit dependencies for classroom HTTP routers and workers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from classroom_sync.auth.fincolab import Principal, StudentChildExperiment
from classroom_sync.services.assignments import AssignmentService
from classroom_sync.services.plans import PlanService


class ClassroomIdentityGateway(Protocol):
    def resolve_principal(self, bearer_token: str) -> Principal: ...

    def require_teacher_owner(
        self, principal: Principal, space_id: str, experiment_id: str
    ) -> None: ...

    def require_student_member(self, principal: Principal, space_id: str) -> None: ...

    def list_student_children(
        self, principal: Principal, space_id: str, parent_algorithm_id: str
    ) -> tuple[StudentChildExperiment, ...]: ...


@dataclass(frozen=True)
class ClassroomServices:
    """Dependencies injected by composition root, never derived from client input."""

    identity_gateway: ClassroomIdentityGateway
    plan_service: PlanService
    assignment_service: AssignmentService
