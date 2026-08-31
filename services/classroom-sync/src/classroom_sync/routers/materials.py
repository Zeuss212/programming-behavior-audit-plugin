"""Teacher-owned, source-free assessment material route."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, Request

from classroom_sync.errors import UpstreamUnavailableError
from classroom_sync.routers.plans import get_services, resolve_bearer_principal
from classroom_sync.services.assessment_materials import AssessmentMaterialBundle

router = APIRouter(prefix="/v1/classroom", tags=["classroom-teacher"])


@router.get(
    "/experiments/{space_id}/{parent_algorithm_id}/assessment-materials",
    response_model=AssessmentMaterialBundle,
)
def get_assessment_materials(
    space_id: str,
    parent_algorithm_id: str,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> AssessmentMaterialBundle:
    """Authorize the parent owner before touching its private material adapter."""

    services = get_services(request)
    principal = resolve_bearer_principal(services, authorization)
    services.identity_gateway.require_teacher_owner(principal, space_id, parent_algorithm_id)
    material_service = services.assessment_material_service
    if material_service is None:
        raise UpstreamUnavailableError(
            "assessment_materials_not_configured", retryable=False
        )
    return material_service.get_bundle(principal, space_id, parent_algorithm_id)
