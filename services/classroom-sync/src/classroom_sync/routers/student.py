"""Student assignment acceptance endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, Request

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
