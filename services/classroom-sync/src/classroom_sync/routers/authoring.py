"""Teacher-owned, resumable plan-authoring session HTTP boundaries."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from classroom_sync.application import ClassroomServices
from classroom_sync.auth.fincolab import Principal
from classroom_sync.errors import AiSuggestionUnavailableError
from classroom_sync.routers.plans import get_services, resolve_bearer_principal
from classroom_sync.services.plan_authoring import (
    AuthoringSuggestionSnapshot,
    PlanAuthoringService,
    PlanAuthoringSnapshot,
)
from classroom_sync.services.plan_suggestions import PlanSuggestionInput

router = APIRouter(
    prefix="/v1/classroom/plan-authoring-sessions",
    tags=["classroom-teacher"],
)


class CreatePlanAuthoringSessionRequest(BaseModel):
    """The parent scope used to resume or create one teacher authoring session."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    space_id: str = Field(min_length=1, max_length=200)
    parent_algorithm_id: str = Field(min_length=1, max_length=200)


def _get_authoring_service(services: ClassroomServices) -> PlanAuthoringService:
    authoring_service = services.plan_authoring_service
    if authoring_service is None:
        raise AiSuggestionUnavailableError("ai_suggestion_not_configured")
    return authoring_service


def _suggestion_response(snapshot: AuthoringSuggestionSnapshot) -> dict[str, object]:
    """Expose safe status and result fields, never provider input or metadata."""

    response: dict[str, object] = {
        "status": snapshot.status,
        "job_id": snapshot.job_id,
        "input_hash": snapshot.input_hash,
    }
    if snapshot.status == "ready" and snapshot.suggestion is not None:
        response["suggestion"] = {
            "title": snapshot.suggestion.title,
            "knowledge_points": [
                point.model_dump(exclude_none=True)
                for point in snapshot.suggestion.knowledge_points
            ],
        }
    elif snapshot.status == "failed":
        response["failure_code"] = snapshot.failure_code
    return response


def _authoring_response(snapshot: PlanAuthoringSnapshot) -> dict[str, object]:
    return {
        "authoring_session_id": snapshot.authoring_session_id,
        "status": snapshot.status,
        "space_id": snapshot.space_id,
        "parent_algorithm_id": snapshot.parent_algorithm_id,
        "draft_id": snapshot.draft_id,
        "suggestion": _suggestion_response(snapshot.suggestion),
    }


def _owned_session_scope(
    request: Request,
    authoring_session_id: str,
    authorization: str | None,
) -> tuple[ClassroomServices, Principal, PlanAuthoringService]:
    """Resolve identity, verify session owner, then recheck current parent ownership."""

    services = get_services(request)
    principal = resolve_bearer_principal(services, authorization)
    authoring_service = _get_authoring_service(services)
    space_id, parent_algorithm_id = authoring_service.get_owned_parent_scope(
        authoring_session_id, teacher_id=principal.user_id
    )
    services.identity_gateway.require_teacher_owner(principal, space_id, parent_algorithm_id)
    return services, principal, authoring_service


@router.post("")
def create_plan_authoring_session(
    payload: CreatePlanAuthoringSessionRequest,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    """Idempotently create or resume one open session after parent authorization."""

    services = get_services(request)
    principal = resolve_bearer_principal(services, authorization)
    services.identity_gateway.require_teacher_owner(
        principal, payload.space_id, payload.parent_algorithm_id
    )
    snapshot = _get_authoring_service(services).create_or_return_open(
        teacher_id=principal.user_id,
        space_id=payload.space_id,
        parent_algorithm_id=payload.parent_algorithm_id,
    )
    return _authoring_response(snapshot)


@router.get("/current")
def get_current_plan_authoring_session(
    request: Request,
    space_id: Annotated[str, Query(min_length=1, max_length=200)],
    parent_algorithm_id: Annotated[str, Query(min_length=1, max_length=200)],
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    """Recover the current open session only after current parent authorization."""

    services = get_services(request)
    principal = resolve_bearer_principal(services, authorization)
    services.identity_gateway.require_teacher_owner(principal, space_id, parent_algorithm_id)
    snapshot = _get_authoring_service(services).get_current(
        teacher_id=principal.user_id,
        space_id=space_id,
        parent_algorithm_id=parent_algorithm_id,
    )
    return _authoring_response(snapshot)


@router.post("/{authoring_session_id}/plan-suggestion", status_code=status.HTTP_202_ACCEPTED)
def create_plan_authoring_suggestion(
    authoring_session_id: str,
    payload: PlanSuggestionInput,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    """Create the session's first suggestion or return its durable original attempt."""

    _services, principal, authoring_service = _owned_session_scope(
        request, authoring_session_id, authorization
    )
    snapshot = authoring_service.request_suggestion(
        authoring_session_id,
        teacher_id=principal.user_id,
        suggestion_input=payload,
    )
    return _authoring_response(snapshot)


@router.get("/{authoring_session_id}/plan-suggestion")
def get_plan_authoring_suggestion(
    authoring_session_id: str,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    """Refresh one suggestion only after both session-owner and parent checks."""

    _services, principal, authoring_service = _owned_session_scope(
        request, authoring_session_id, authorization
    )
    snapshot = authoring_service.get_owned(authoring_session_id, teacher_id=principal.user_id)
    return _authoring_response(snapshot)


@router.post("/{authoring_session_id}/abandon")
def abandon_plan_authoring_session(
    authoring_session_id: str,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    """Abandon an owned open session only while parent ownership remains valid."""

    _services, principal, authoring_service = _owned_session_scope(
        request, authoring_session_id, authorization
    )
    snapshot = authoring_service.abandon(authoring_session_id, teacher_id=principal.user_id)
    return _authoring_response(snapshot)
