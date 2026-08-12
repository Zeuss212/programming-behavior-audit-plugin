"""Teacher plan publication and server-side student assignment synchronization."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Header, Request, status
from pydantic import BaseModel, ConfigDict

from classroom_sync.application import ClassroomServices
from classroom_sync.auth.fincolab import Principal
from classroom_sync.errors import AuthenticationError
from classroom_sync.services.plans import PlanDraftInput

router = APIRouter(prefix="/v1/classroom/plans", tags=["classroom-teacher"])


class CreatePlanDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    space_id: str
    parent_algorithm_id: str
    title: str
    profile: dict[str, object]
    scheduled_start_at: datetime
    scheduled_end_at: datetime
    ai_policy: str


def get_services(request: Request) -> ClassroomServices:
    services = getattr(request.app.state, "classroom_services", None)
    if not isinstance(services, ClassroomServices):
        raise TypeError("Classroom services are not configured.")
    return services


def resolve_bearer_principal(
    services: ClassroomServices,
    authorization: str | None,
) -> Principal:
    if authorization is None or not authorization.startswith("Bearer "):
        raise AuthenticationError("missing_bearer_token")
    return services.identity_gateway.resolve_principal(authorization.removeprefix("Bearer "))


@router.post("/drafts", status_code=status.HTTP_201_CREATED)
def create_plan_draft(
    payload: CreatePlanDraftRequest,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    services = get_services(request)
    principal = resolve_bearer_principal(services, authorization)
    services.identity_gateway.require_teacher_owner(
        principal, payload.space_id, payload.parent_algorithm_id
    )
    draft = services.plan_service.create_draft(
        PlanDraftInput(
            space_id=payload.space_id,
            parent_algorithm_id=payload.parent_algorithm_id,
            title=payload.title,
            profile=payload.profile,
            scheduled_start_at=payload.scheduled_start_at,
            scheduled_end_at=payload.scheduled_end_at,
            ai_policy=payload.ai_policy,
        ),
        teacher_id=principal.user_id,
    )
    return {"draft_id": draft.id, "revision": draft.revision}


@router.post("/drafts/{draft_id}/publish")
def publish_plan_draft(
    draft_id: str,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    services = get_services(request)
    principal = resolve_bearer_principal(services, authorization)
    draft = services.plan_service.get_draft(draft_id)
    services.identity_gateway.require_teacher_owner(
        principal, draft.space_id, draft.parent_algorithm_id
    )
    published = services.plan_service.publish_draft(draft_id, teacher_id=principal.user_id)
    return {
        "plan_version_id": published.id,
        "plan_id": published.plan_id,
        "version": published.version,
        "content_hash": published.content_hash,
    }


@router.post("/{plan_version_id}/assignments/sync")
def synchronize_assignments(
    plan_version_id: str,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    services = get_services(request)
    principal = resolve_bearer_principal(services, authorization)
    plan_version = services.plan_service.get_plan_version(plan_version_id)
    services.identity_gateway.require_teacher_owner(
        principal, plan_version.space_id, plan_version.parent_algorithm_id
    )
    roster = services.identity_gateway.list_student_children(
        principal, plan_version.space_id, plan_version.parent_algorithm_id
    )
    assignments = services.assignment_service.sync_assignments(plan_version, roster)
    return {
        "assignments": [
            {
                "assignment_id": assignment.id,
                "student_id": assignment.student_id,
                "plan_version": assignment.plan_version,
                "status": assignment.status,
            }
            for assignment in assignments
        ]
    }
