"""Teacher-authorized, transient AI suggestions for classroom plan authoring."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, Request
from pydantic import BaseModel, ConfigDict, Field

from classroom_sync.errors import AiSuggestionUnavailableError
from classroom_sync.routers.plans import get_services, resolve_bearer_principal
from classroom_sync.services.plan_suggestion_jobs import PlanSuggestionJobSnapshot
from classroom_sync.services.plan_suggestions import PlanSuggestionInput

router = APIRouter(prefix="/v1/classroom", tags=["classroom-teacher"])


class PlanSuggestionRequest(BaseModel):
    """Only the selected experiment and bounded teaching text reach the provider."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    space_id: str = Field(min_length=1, max_length=200)
    parent_algorithm_id: str = Field(min_length=1, max_length=200)
    title: str = Field(max_length=200)
    statement: str = Field(min_length=1, max_length=10_000)


def _job_response(snapshot: PlanSuggestionJobSnapshot) -> dict[str, object]:
    """Keep task polling free of teacher source text and provider metadata."""

    response: dict[str, object] = {"job_id": snapshot.job_id, "status": snapshot.status}
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


@router.post("/plan-suggestions", status_code=202)
def create_plan_suggestion(
    payload: PlanSuggestionRequest,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    """Queue an owner-scoped suggestion instead of holding a provider request open."""

    services = get_services(request)
    principal = resolve_bearer_principal(services, authorization)
    services.identity_gateway.require_teacher_owner(
        principal, payload.space_id, payload.parent_algorithm_id
    )
    suggestion_job_service = services.plan_suggestion_job_service
    if suggestion_job_service is None:
        raise AiSuggestionUnavailableError("ai_suggestion_not_configured")
    snapshot = suggestion_job_service.submit(
        teacher_id=principal.user_id,
        space_id=payload.space_id,
        parent_algorithm_id=payload.parent_algorithm_id,
        suggestion_input=PlanSuggestionInput(title=payload.title, statement=payload.statement),
    )
    return _job_response(snapshot)


@router.get("/plan-suggestions/{job_id}")
def get_plan_suggestion(
    job_id: str,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    """Poll a previously authorized task without re-sending the teacher prompt."""

    services = get_services(request)
    principal = resolve_bearer_principal(services, authorization)
    suggestion_job_service = services.plan_suggestion_job_service
    if suggestion_job_service is None:
        raise AiSuggestionUnavailableError("ai_suggestion_not_configured")
    return _job_response(suggestion_job_service.get_for_teacher(job_id, teacher_id=principal.user_id))
