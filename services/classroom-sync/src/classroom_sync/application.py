"""Explicit dependencies for classroom HTTP routers and workers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from classroom_sync.auth.fincolab import Principal, StudentChildExperiment
from classroom_sync.services.assignments import AssignmentService
from classroom_sync.services.briefs import BriefService
from classroom_sync.services.deadlines import DeadlineService
from classroom_sync.services.plan_suggestions import PlanSuggestion, PlanSuggestionInput
from classroom_sync.services.plans import PlanService
from classroom_sync.services.read_models import ClassroomReadService
from classroom_sync.services.sessions import PluginSessionService


class ClassroomIdentityGateway(Protocol):
    def resolve_principal(self, bearer_token: str) -> Principal: ...

    def require_teacher_owner(
        self, principal: Principal, space_id: str, experiment_id: str
    ) -> None: ...

    def require_student_member(self, principal: Principal, space_id: str) -> None: ...

    def list_student_children(
        self, principal: Principal, space_id: str, parent_algorithm_id: str
    ) -> tuple[StudentChildExperiment, ...]: ...


class ClassroomPlanSuggestionService(Protocol):
    """A transient, teacher-authorized plan drafting capability."""

    def generate(self, suggestion_input: PlanSuggestionInput) -> PlanSuggestion: ...


@dataclass(frozen=True)
class ClassroomServices:
    """Dependencies injected by composition root, never derived from client input."""

    identity_gateway: ClassroomIdentityGateway
    plan_service: PlanService
    assignment_service: AssignmentService
    plugin_session_service: PluginSessionService | None = None
    brief_service: BriefService | None = None
    deadline_service: DeadlineService | None = None
    read_service: ClassroomReadService | None = None
    plan_suggestion_service: ClassroomPlanSuggestionService | None = None
