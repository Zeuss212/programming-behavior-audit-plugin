"""Teacher-authorized, transient AI suggestions for classroom plan authoring."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, Request
from pydantic import BaseModel, ConfigDict, Field

from classroom_sync.errors import AiSuggestionUnavailableError
from classroom_sync.routers.plans import get_services, resolve_bearer_principal
from classroom_sync.services.plan_suggestions import PlanSuggestionInput

router = APIRouter(prefix="/v1/classroom", tags=["classroom-teacher"])


class PlanSuggestionRequest(BaseModel):
    """Only the selected experiment and bounded teaching text reach the provider."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    space_id: str = Field(min_length=1, max_length=200)
    parent_algorithm_id: str = Field(min_length=1, max_length=200)
    title: str = Field(max_length=200)
    statement: str = Field(min_length=1, max_length=10_000)


@router.post("/plan-suggestions")
def create_plan_suggestion(
    payload: PlanSuggestionRequest,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    """Generate a preview only after the bearer proves teacher ownership."""

    services = get_services(request)
    principal = resolve_bearer_principal(services, authorization)
    services.identity_gateway.require_teacher_owner(
        principal, payload.space_id, payload.parent_algorithm_id
    )
    suggestion_service = services.plan_suggestion_service
    if suggestion_service is None:
        raise AiSuggestionUnavailableError("ai_suggestion_not_configured")
    suggestion = suggestion_service.generate(
        PlanSuggestionInput(title=payload.title, statement=payload.statement)
    )
    return {
        "title": suggestion.title,
        "knowledge_points": [point.model_dump() for point in suggestion.knowledge_points],
    }
