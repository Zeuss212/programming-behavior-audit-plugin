"""HTTP boundary for independent draft assessment configuration."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Header, Request
from pydantic import BaseModel, ConfigDict, Field

from classroom_sync.application import ClassroomServices
from classroom_sync.routers.plans import (
    _draft_response,
    get_read_service,
    get_services,
    resolve_bearer_principal,
)
from classroom_sync.services.assessment_configs import (
    AssessmentConfigService,
    AssessmentConfigSnapshot,
)
from classroom_sync.services.plans import PlanDraftInput
from classroom_sync.services.publication_gate import PublicationGateResult

router = APIRouter(
    prefix="/v1/classroom/plans/drafts",
    tags=["classroom-teacher"],
)
assessment_draft_router = APIRouter(
    prefix="/v1/classroom/experiments",
    tags=["classroom-teacher"],
)


class UpdateAssessmentConfigRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_draft_revision: int = Field(strict=True, ge=0)
    expected_config_revision: int = Field(strict=True, ge=0)
    monitoring_scopes: dict[str, object]
    evaluation_dimensions: list[dict[str, object]]


def _service(services: ClassroomServices) -> AssessmentConfigService:
    service = services.assessment_config_service
    if service is None:
        raise TypeError("Assessment config service is not configured.")
    return service


def _response(snapshot: AssessmentConfigSnapshot) -> dict[str, object]:
    return {
        "draft_id": snapshot.draft_id,
        "draft_revision": snapshot.draft_revision,
        "config_revision": snapshot.config_revision,
        "schema_version": snapshot.schema_version,
        "monitoring_scopes": snapshot.monitoring_scopes,
        "evaluation_dimensions": snapshot.evaluation_dimensions,
        "total_bps": snapshot.total_bps,
    }


def _authorized_draft(
    services: ClassroomServices,
    draft_id: str,
    authorization: str | None,
) -> tuple[str, str, str]:
    principal = resolve_bearer_principal(services, authorization)
    draft = services.plan_service.get_draft(draft_id, teacher_id=principal.user_id)
    services.identity_gateway.require_teacher_owner(
        principal, draft.space_id, draft.parent_algorithm_id
    )
    return principal.user_id, draft.space_id, draft.parent_algorithm_id


@router.get("/{draft_id}/assessment-config")
def get_assessment_config(
    draft_id: str,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    services = get_services(request)
    teacher_id, _space_id, _parent_algorithm_id = _authorized_draft(
        services, draft_id, authorization
    )
    return _response(_service(services).get_or_create(draft_id, teacher_id=teacher_id))


@router.put("/{draft_id}/assessment-config")
def update_assessment_config(
    draft_id: str,
    payload: UpdateAssessmentConfigRequest,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    services = get_services(request)
    teacher_id, _space_id, _parent_algorithm_id = _authorized_draft(
        services, draft_id, authorization
    )
    snapshot = _service(services).update(
        draft_id,
        teacher_id=teacher_id,
        expected_draft_revision=payload.expected_draft_revision,
        expected_config_revision=payload.expected_config_revision,
        monitoring_scopes=payload.monitoring_scopes,
        evaluation_dimensions=payload.evaluation_dimensions,
    )
    return _response(snapshot)


@assessment_draft_router.post(
    "/{space_id}/{parent_algorithm_id}/assessment-drafts"
)
def create_or_recover_assessment_draft(
    space_id: str,
    parent_algorithm_id: str,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    """Resume one editor draft, deriving immutable fields only from a bound version."""

    services = get_services(request)
    principal = resolve_bearer_principal(services, authorization)
    services.identity_gateway.require_teacher_owner(
        principal, space_id, parent_algorithm_id
    )
    authoring_service = services.plan_authoring_service
    if authoring_service is None:
        raise TypeError("Plan authoring service is not configured.")
    authoring = authoring_service.create_or_return_open(
        teacher_id=principal.user_id,
        space_id=space_id,
        parent_algorithm_id=parent_algorithm_id,
    )
    if authoring.draft_id is not None:
        draft = services.plan_service.get_draft(
            authoring.draft_id, teacher_id=principal.user_id
        )
    else:
        source = get_read_service(request).get_experiment_plan(
            space_id, parent_algorithm_id
        )
        profile = source.get("profile")
        title = source.get("title")
        scheduled_start_at = source.get("scheduled_start_at")
        scheduled_end_at = source.get("scheduled_end_at")
        ai_policy = source.get("ai_policy")
        if (
            not isinstance(profile, dict)
            or not isinstance(title, str)
            or not isinstance(scheduled_start_at, str)
            or not isinstance(scheduled_end_at, str)
            or not isinstance(ai_policy, str)
        ):
            raise TypeError("Published plan summary is invalid.")
        draft_profile = {
            key: value
            for key, value in profile.items()
            if key
            not in {
                "profile_id",
                "version",
                "content_hash",
                "deployment_status",
                "preview_status",
            }
        }
        draft = services.plan_service.create_draft(
            PlanDraftInput(
                authoring_session_id=authoring.authoring_session_id,
                space_id=space_id,
                parent_algorithm_id=parent_algorithm_id,
                title=title,
                profile=draft_profile,
                scheduled_start_at=datetime.fromisoformat(scheduled_start_at),
                scheduled_end_at=datetime.fromisoformat(scheduled_end_at),
                ai_policy=ai_policy,
            ),
            teacher_id=principal.user_id,
        )
    return _draft_response(
        draft,
        PublicationGateResult(
            status="ready",
            blocking_count=0,
            warning_count=0,
            issues=(),
        ),
    )
