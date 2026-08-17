"""Teacher classroom-monitoring Server-Sent Events."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Header, Request
from fastapi.responses import StreamingResponse

from classroom_sync.routers.plans import (
    get_read_service,
    get_services,
    resolve_bearer_principal,
)
from classroom_sync.services.read_models import monitoring_event_frame

router = APIRouter(prefix="/v1/classroom/classrooms", tags=["classroom-teacher"])


@router.get("/{plan_version_id}/events")
async def stream_monitoring(
    plan_version_id: str,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> StreamingResponse:
    """Stream a fresh, teacher-authorized snapshot at most once every ten seconds."""

    services = get_services(request)
    principal = resolve_bearer_principal(services, authorization)
    plan_version = services.plan_service.get_plan_version(plan_version_id)
    services.identity_gateway.require_teacher_owner(
        principal, plan_version.space_id, plan_version.parent_algorithm_id
    )
    read_service = get_read_service(request)

    async def event_stream() -> AsyncIterator[str]:
        while not await request.is_disconnected():
            yield monitoring_event_frame(read_service.get_teacher_monitoring(plan_version_id))
            await asyncio.sleep(10)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
