"""Teacher management of private experiment resources."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Header, Query, Request, Response, status

from classroom_sync.errors import UpstreamUnavailableError, ValidationError
from classroom_sync.routers.plans import get_services, resolve_bearer_principal
from classroom_sync.services.experiment_resources import (
    MAX_RESOURCE_BYTES,
    ExperimentResourceMetadata,
    ExperimentResourceService,
    ResourceKind,
)

router = APIRouter(prefix="/v1/classroom/experiments", tags=["classroom-teacher"])


def get_resource_service(request: Request) -> ExperimentResourceService:
    service = get_services(request).experiment_resource_service
    if service is None:
        raise UpstreamUnavailableError("experiment_resources_not_configured", retryable=False)
    return service


async def read_limited_body(request: Request) -> bytes:
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            length = int(declared)
        except ValueError as error:
            raise ValidationError("experiment_resource_content_length_invalid") from error
        if length < 0 or length > MAX_RESOURCE_BYTES:
            raise ValidationError("experiment_resource_too_large")
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > MAX_RESOURCE_BYTES:
            raise ValidationError("experiment_resource_too_large")
        chunks.append(chunk)
    return b"".join(chunks)


def wire_datetime(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def resource_response(resource: ExperimentResourceMetadata) -> dict[str, object]:
    return {
        "id": resource.id,
        "resource_kind": resource.resource_kind,
        "filename": resource.filename,
        "content_type": resource.content_type,
        "size_bytes": resource.size_bytes,
        "sha256": resource.sha256,
        "download_only": resource.download_only,
        "created_at": wire_datetime(resource.created_at),
    }


@router.get("/{space_id}/{parent_algorithm_id}/resources")
def list_resources(
    space_id: str,
    parent_algorithm_id: str,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    services = get_services(request)
    principal = resolve_bearer_principal(services, authorization)
    services.identity_gateway.require_teacher_owner(principal, space_id, parent_algorithm_id)
    resources = get_resource_service(request).list_resources(space_id, parent_algorithm_id)
    return {"schema_version": 1, "resources": [resource_response(item) for item in resources]}


@router.post(
    "/{space_id}/{parent_algorithm_id}/resources/{resource_kind}",
    status_code=status.HTTP_201_CREATED,
)
async def upload_resource(
    space_id: str,
    parent_algorithm_id: str,
    resource_kind: ResourceKind,
    request: Request,
    filename: Annotated[str, Query(min_length=1, max_length=255)],
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    services = get_services(request)
    principal = resolve_bearer_principal(services, authorization)
    services.identity_gateway.require_teacher_owner(principal, space_id, parent_algorithm_id)
    resource = get_resource_service(request).upload(
        space_id=space_id,
        parent_algorithm_id=parent_algorithm_id,
        teacher_id=principal.user_id,
        resource_kind=resource_kind,
        filename=filename,
        body=await read_limited_body(request),
    )
    return {"schema_version": 1, "resource": resource_response(resource)}


@router.get("/{space_id}/{parent_algorithm_id}/resources/{resource_id}/download")
def download_resource(
    space_id: str,
    parent_algorithm_id: str,
    resource_id: str,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> Response:
    services = get_services(request)
    principal = resolve_bearer_principal(services, authorization)
    services.identity_gateway.require_teacher_owner(principal, space_id, parent_algorithm_id)
    download = get_resource_service(request).download(space_id, parent_algorithm_id, resource_id)
    disposition = f"attachment; filename=download; filename*=UTF-8''{quote(download.resource.filename, safe='')}"
    return Response(
        content=download.body,
        media_type=download.resource.content_type,
        headers={"Content-Disposition": disposition, "X-Content-Type-Options": "nosniff"},
    )


@router.delete("/{space_id}/{parent_algorithm_id}/resources/{resource_id}")
def delete_resource(
    space_id: str,
    parent_algorithm_id: str,
    resource_id: str,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> Response:
    services = get_services(request)
    principal = resolve_bearer_principal(services, authorization)
    services.identity_gateway.require_teacher_owner(principal, space_id, parent_algorithm_id)
    get_resource_service(request).delete(
        space_id=space_id,
        parent_algorithm_id=parent_algorithm_id,
        teacher_id=principal.user_id,
        resource_id=resource_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
