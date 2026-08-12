"""Teacher-only access to final briefs and separate review overlays."""

from __future__ import annotations

from typing import Annotated, cast

from fastapi import APIRouter, Header, Request, status
from pydantic import BaseModel, ConfigDict

from classroom_sync.application import ClassroomServices
from classroom_sync.auth.fincolab import Principal
from classroom_sync.routers.plans import get_services, resolve_bearer_principal
from classroom_sync.services.briefs import TeacherReviewInput

router = APIRouter(prefix="/v1/classroom/teacher", tags=["classroom-teacher"])


class TeacherReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    knowledge_point_reviews: list[dict[str, object]]
    comment: str


def require_teacher_for_session(
    request: Request,
    authorization: str | None,
    session_id: str,
) -> tuple[ClassroomServices, Principal]:
    services = get_services(request)
    if services.brief_service is None:
        raise TypeError("Brief service is not configured.")
    principal = resolve_bearer_principal(services, authorization)
    assignment = services.brief_service.get_assignment_for_session(session_id)
    services.identity_gateway.require_teacher_owner(
        principal, assignment.space_id, assignment.parent_algorithm_id
    )
    return services, principal


@router.get("/sessions/{session_id}/brief")
def get_student_brief(
    session_id: str,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    services, _principal = require_teacher_for_session(request, authorization, session_id)
    if services.brief_service is None:
        raise TypeError("Brief service is not configured.")
    return cast(dict[str, object], services.brief_service.get_latest_brief(session_id).payload)


@router.post("/sessions/{session_id}/reviews", status_code=status.HTTP_201_CREATED)
def review_student_brief(
    session_id: str,
    payload: TeacherReviewRequest,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    services, principal = require_teacher_for_session(request, authorization, session_id)
    if services.brief_service is None:
        raise TypeError("Brief service is not configured.")
    review = services.brief_service.review(
        session_id,
        teacher_id=principal.user_id,
        review_input=TeacherReviewInput(
            knowledge_point_reviews=tuple(payload.knowledge_point_reviews),
            comment=payload.comment,
        ),
    )
    return cast(dict[str, object], review.payload)
