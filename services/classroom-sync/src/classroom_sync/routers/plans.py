"""Teacher plan publication and server-side student assignment synchronization."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Header, Request, status
from pydantic import BaseModel, ConfigDict, Field

from classroom_sync.application import ClassroomServices
from classroom_sync.auth.fincolab import Principal
from classroom_sync.errors import AuthenticationError, UpstreamUnavailableError
from classroom_sync.models import PlanDraft
from classroom_sync.services.assessment_materials import AssessmentMaterialBundle
from classroom_sync.services.plans import PlanDraftInput
from classroom_sync.services.publication_gate import PublicationGate, PublicationGateResult
from classroom_sync.services.read_models import ClassroomReadService

router = APIRouter(prefix="/v1/classroom/plans", tags=["classroom-teacher"])
publication_gate = PublicationGate()


class CreatePlanDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    authoring_session_id: str | None = None
    space_id: str
    parent_algorithm_id: str
    title: str
    profile: dict[str, object]
    scheduled_start_at: datetime
    scheduled_end_at: datetime
    ai_policy: str


class UpdatePlanDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=0)
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


def get_read_service(request: Request) -> ClassroomReadService:
    """Resolve the read-model dependency only for page-facing endpoints."""

    read_service = get_services(request).read_service
    if read_service is None:
        raise TypeError("Classroom read service is not configured.")
    return read_service


def resolve_bearer_principal(
    services: ClassroomServices,
    authorization: str | None,
) -> Principal:
    if authorization is None or not authorization.startswith("Bearer "):
        raise AuthenticationError("missing_bearer_token")
    return services.identity_gateway.resolve_principal(authorization.removeprefix("Bearer "))


def _latest_materials(
    services: ClassroomServices,
    principal: Principal,
    *,
    space_id: str,
    parent_algorithm_id: str,
) -> AssessmentMaterialBundle:
    material_service = services.assessment_material_service
    if material_service is None:
        raise UpstreamUnavailableError(
            "assessment_materials_not_configured",
            retryable=False,
        )
    return material_service.get_bundle(principal, space_id, parent_algorithm_id)


def _gate_for_draft(
    services: ClassroomServices,
    principal: Principal,
    draft: PlanDraft,
) -> PublicationGateResult:
    if draft.profile.get("schema_version") != 3:
        return PublicationGateResult(
            status="ready",
            blocking_count=0,
            warning_count=0,
            issues=(),
        )
    materials = _latest_materials(
        services,
        principal,
        space_id=draft.space_id,
        parent_algorithm_id=draft.parent_algorithm_id,
    )
    return publication_gate.evaluate(draft.profile, materials)


def _draft_response(
    draft: PlanDraft,
    gate: PublicationGateResult,
) -> dict[str, object]:
    return {
        "draft_id": draft.id,
        "authoring_session_id": draft.authoring_session_id,
        "space_id": draft.space_id,
        "parent_algorithm_id": draft.parent_algorithm_id,
        "title": draft.title,
        "profile": draft.profile,
        "scheduled_start_at": _wire_datetime(draft.scheduled_start_at),
        "scheduled_end_at": _wire_datetime(draft.scheduled_end_at),
        "ai_policy": draft.ai_policy,
        "revision": draft.revision,
        "publication_gate": gate.model_dump(mode="json"),
    }


def _wire_datetime(value: datetime) -> datetime:
    """Keep draft recovery timestamps stable with timezone-poor test databases."""

    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


@router.get("/experiments/{space_id}/{parent_algorithm_id}")
def get_experiment_plan(
    space_id: str,
    parent_algorithm_id: str,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    """Read a teacher-owned experiment's active classroom plan."""

    services = get_services(request)
    principal = resolve_bearer_principal(services, authorization)
    services.identity_gateway.require_teacher_owner(principal, space_id, parent_algorithm_id)
    return get_read_service(request).get_experiment_plan(space_id, parent_algorithm_id)


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
            authoring_session_id=payload.authoring_session_id,
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
    return _draft_response(draft, _gate_for_draft(services, principal, draft))


@router.get("/drafts/{draft_id}")
def get_plan_draft(
    draft_id: str,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    services = get_services(request)
    principal = resolve_bearer_principal(services, authorization)
    draft = services.plan_service.get_draft(
        draft_id,
        teacher_id=principal.user_id,
    )
    services.identity_gateway.require_teacher_owner(
        principal,
        draft.space_id,
        draft.parent_algorithm_id,
    )
    return _draft_response(draft, _gate_for_draft(services, principal, draft))


@router.put("/drafts/{draft_id}")
def update_plan_draft(
    draft_id: str,
    payload: UpdatePlanDraftRequest,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    services = get_services(request)
    principal = resolve_bearer_principal(services, authorization)
    current = services.plan_service.get_draft(
        draft_id,
        teacher_id=principal.user_id,
    )
    services.identity_gateway.require_teacher_owner(
        principal,
        current.space_id,
        current.parent_algorithm_id,
    )
    materials = None
    if payload.profile.get("schema_version") == 3:
        materials = _latest_materials(
            services,
            principal,
            space_id=current.space_id,
            parent_algorithm_id=current.parent_algorithm_id,
        )
    draft = services.plan_service.update_draft(
        draft_id,
        profile=payload.profile,
        teacher_id=principal.user_id,
        title=payload.title,
        scheduled_start_at=payload.scheduled_start_at,
        scheduled_end_at=payload.scheduled_end_at,
        ai_policy=payload.ai_policy,
        expected_revision=payload.expected_revision,
    )
    gate = (
        publication_gate.evaluate(draft.profile, materials)
        if materials is not None
        else PublicationGateResult(
            status="ready",
            blocking_count=0,
            warning_count=0,
            issues=(),
        )
    )
    return _draft_response(draft, gate)


@router.post("/drafts/{draft_id}/publish")
def publish_plan_draft(
    draft_id: str,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    services = get_services(request)
    principal = resolve_bearer_principal(services, authorization)
    draft = services.plan_service.get_draft(
        draft_id,
        teacher_id=principal.user_id,
    )
    services.identity_gateway.require_teacher_owner(
        principal, draft.space_id, draft.parent_algorithm_id
    )
    materials = None
    if draft.profile.get("schema_version") == 3:
        materials = _latest_materials(
            services,
            principal,
            space_id=draft.space_id,
            parent_algorithm_id=draft.parent_algorithm_id,
        )
    published = services.plan_service.publish_draft(
        draft_id,
        teacher_id=principal.user_id,
        materials=materials,
    )
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
