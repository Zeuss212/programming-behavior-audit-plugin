"""Student assignment acceptance endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, Request, status

from classroom_sync.errors import AuthorizationError
from classroom_sync.routers.plans import get_services, resolve_bearer_principal

router = APIRouter(prefix="/v1/classroom/student", tags=["classroom-student"])


@router.post("/assignments/{assignment_id}/accept")
def accept_assignment(
    assignment_id: str,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    services = get_services(request)
    principal = resolve_bearer_principal(services, authorization)
    assignment = services.assignment_service.get_assignment(assignment_id)
    services.identity_gateway.require_student_member(principal, assignment.space_id)
    accepted = services.assignment_service.accept_assignment(
        assignment_id, student_id=principal.user_id
    )
    return {
        "assignment_id": accepted.id,
        "status": accepted.status,
        "plan_version": accepted.plan_version,
    }


@router.post("/assignments/{assignment_id}/launch-ticket", status_code=status.HTTP_201_CREATED)
def issue_launch_ticket(
    assignment_id: str,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    services = get_services(request)
    if services.plugin_session_service is None:
        raise TypeError("Plugin session service is not configured.")
    principal = resolve_bearer_principal(services, authorization)
    assignment = services.assignment_service.get_assignment(assignment_id)
    services.identity_gateway.require_student_member(principal, assignment.space_id)
    if assignment.student_id != principal.user_id:
        raise AuthorizationError("student_assignment_owner_mismatch")
    issued = services.plugin_session_service.issue_ticket(assignment_id)
    return {"ticket": issued.ticket, "expires_at": issued.expires_at.isoformat()}
