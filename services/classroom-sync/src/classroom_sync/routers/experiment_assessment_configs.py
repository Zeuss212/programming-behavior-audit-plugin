"""Teacher API for assessment content bound directly to an experiment."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, Request
from pydantic import BaseModel, ConfigDict, Field

from classroom_sync.errors import UpstreamUnavailableError
from classroom_sync.routers.plans import get_services, resolve_bearer_principal
from classroom_sync.services.experiment_assessment_configs import (
    ExperimentAssessmentConfigService,
    ExperimentAssessmentConfigSnapshot,
)

router = APIRouter(prefix="/v1/classroom/experiments", tags=["classroom-teacher"])


class EnsureExperimentAssessmentConfigRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    experiment_name: str = Field(min_length=1, max_length=200)


class UpdateExperimentAssessmentConfigRequest(EnsureExperimentAssessmentConfigRequest):
    expected_config_revision: int = Field(strict=True, ge=0)
    monitoring_scopes: dict[str, object]
    evaluation_dimensions: list[dict[str, object]]


def service(request: Request) -> ExperimentAssessmentConfigService:
    configured = get_services(request).experiment_assessment_config_service
    if configured is None:
        raise UpstreamUnavailableError(
            "experiment_assessment_configs_not_configured", retryable=False
        )
    return configured


def response(snapshot: ExperimentAssessmentConfigSnapshot) -> dict[str, object]:
    return {
        "schema_version": snapshot.schema_version,
        "space_id": snapshot.space_id,
        "parent_algorithm_id": snapshot.parent_algorithm_id,
        "experiment_name": snapshot.experiment_name,
        "config_revision": snapshot.config_revision,
        "monitoring_scopes": snapshot.monitoring_scopes,
        "evaluation_dimensions": snapshot.evaluation_dimensions,
        "total_bps": snapshot.total_bps,
    }


def authorize(
    request: Request,
    authorization: str | None,
    space_id: str,
    parent_algorithm_id: str,
) -> str:
    services = get_services(request)
    principal = resolve_bearer_principal(services, authorization)
    services.identity_gateway.require_teacher_owner(principal, space_id, parent_algorithm_id)
    return principal.user_id


@router.get("/{space_id}/{parent_algorithm_id}/assessment-config")
def get_config(
    space_id: str,
    parent_algorithm_id: str,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    authorize(request, authorization, space_id, parent_algorithm_id)
    return response(
        service(request).get(
            space_id=space_id,
            parent_algorithm_id=parent_algorithm_id,
        )
    )


@router.post("/{space_id}/{parent_algorithm_id}/assessment-config")
def ensure_config(
    space_id: str,
    parent_algorithm_id: str,
    payload: EnsureExperimentAssessmentConfigRequest,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    teacher_id = authorize(request, authorization, space_id, parent_algorithm_id)
    return response(
        service(request).ensure(
            space_id=space_id,
            parent_algorithm_id=parent_algorithm_id,
            experiment_name=payload.experiment_name,
            teacher_id=teacher_id,
        )
    )


@router.put("/{space_id}/{parent_algorithm_id}/assessment-config")
def update_config(
    space_id: str,
    parent_algorithm_id: str,
    payload: UpdateExperimentAssessmentConfigRequest,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    teacher_id = authorize(request, authorization, space_id, parent_algorithm_id)
    return response(
        service(request).update(
            space_id=space_id,
            parent_algorithm_id=parent_algorithm_id,
            experiment_name=payload.experiment_name,
            teacher_id=teacher_id,
            expected_config_revision=payload.expected_config_revision,
            monitoring_scopes=payload.monitoring_scopes,
            evaluation_dimensions=payload.evaluation_dimensions,
        )
    )
