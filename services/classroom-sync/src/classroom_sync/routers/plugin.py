"""Plugin-only registration, liveness, and evidence upload endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, Header, Request, status
from pydantic import BaseModel, ConfigDict

from classroom_sync.errors import AuthenticationError
from classroom_sync.routers.plans import get_services
from classroom_sync.services.briefs import BriefContent
from classroom_sync.services.sessions import SessionCredentials

router = APIRouter(prefix="/v1/classroom/plugin", tags=["classroom-plugin"])


class RegisterPluginSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticket: str
    plugin_instance_id: str


class SubmitBriefRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str
    knowledge_points: list[dict[str, object]]
    process_overview: list[str]
    issues: list[str]
    ai_analysis_status: str | None = None
    reason: str


def get_plugin_bearer(authorization: str | None) -> str:
    if authorization is None or not authorization.startswith("Bearer "):
        raise AuthenticationError("missing_plugin_token")
    return authorization.removeprefix("Bearer ")


def credentials_response(credentials: SessionCredentials) -> dict[str, object]:
    """Expose the immutable classroom snapshot without leaking ticket material."""

    return {
        "session_id": credentials.session_id,
        "access_token": credentials.access_token,
        "expires_at": credentials.expires_at.isoformat(),
        "assignment_id": credentials.assignment_id,
        "plan_id": credentials.plan_id,
        "plan_version": credentials.plan_version,
        "profile": credentials.profile,
        "scheduled_end_at": credentials.scheduled_end_at.isoformat(),
        "evidence_cutoff_at": credentials.evidence_cutoff_at.isoformat(),
        "last_sync_at": credentials.last_sync_at.isoformat(),
    }


@router.post("/sessions/register", status_code=status.HTTP_201_CREATED)
def register_plugin_session(
    payload: RegisterPluginSessionRequest,
    request: Request,
) -> dict[str, object]:
    services = get_services(request)
    if services.plugin_session_service is None:
        raise TypeError("Plugin session service is not configured.")
    credentials = services.plugin_session_service.register(
        payload.ticket, plugin_instance_id=payload.plugin_instance_id
    )
    return credentials_response(credentials)


@router.post("/sessions/{session_id}/context/refresh")
def refresh_plugin_context(
    session_id: str,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    services = get_services(request)
    if services.plugin_session_service is None:
        raise TypeError("Plugin session service is not configured.")
    credentials = services.plugin_session_service.refresh_plugin_token(
        get_plugin_bearer(authorization), session_id=session_id
    )
    return credentials_response(credentials)


@router.post("/sessions/{session_id}/heartbeat")
def heartbeat(
    session_id: str,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    services = get_services(request)
    if services.plugin_session_service is None:
        raise TypeError("Plugin session service is not configured.")
    monitor_session = services.plugin_session_service.heartbeat(
        get_plugin_bearer(authorization), session_id=session_id
    )
    if monitor_session.last_heartbeat_at is None:
        raise TypeError("Heartbeat result must include last_heartbeat_at.")
    return {
        "session_id": monitor_session.id,
        "status": monitor_session.status,
        "last_heartbeat_at": monitor_session.last_heartbeat_at.isoformat(),
    }


@router.put("/sessions/{session_id}/evidence/{sequence}", status_code=status.HTTP_201_CREATED)
def upload_evidence(
    session_id: str,
    sequence: int,
    request: Request,
    body: Annotated[bytes, Body(media_type="application/gzip")],
    first_event_sequence: Annotated[int, Header(alias="X-First-Event-Sequence")],
    last_event_sequence: Annotated[int, Header(alias="X-Last-Event-Sequence")],
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    services = get_services(request)
    if services.plugin_session_service is None:
        raise TypeError("Plugin session service is not configured.")
    receipt = services.plugin_session_service.put_evidence_chunk(
        get_plugin_bearer(authorization),
        session_id=session_id,
        sequence=sequence,
        body=body,
        first_event_sequence=first_event_sequence,
        last_event_sequence=last_event_sequence,
    )
    return {
        "evidence_id": receipt.id,
        "session_id": receipt.session_id,
        "sequence": receipt.sequence,
        "content_sha256": receipt.content_sha256,
    }


@router.post("/sessions/{session_id}/submit", status_code=status.HTTP_201_CREATED)
def submit_brief(
    session_id: str,
    payload: SubmitBriefRequest,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    services = get_services(request)
    if services.plugin_session_service is None or services.brief_service is None:
        raise TypeError("Plugin brief dependencies are not configured.")
    access_token = get_plugin_bearer(authorization)
    services.plugin_session_service.authorize_plugin_session(access_token, session_id=session_id)
    brief = services.brief_service.submit(
        session_id,
        BriefContent(
            summary=payload.summary,
            knowledge_points=tuple(payload.knowledge_points),
            process_overview=tuple(payload.process_overview),
            issues=tuple(payload.issues),
        ),
        reason=payload.reason,
        request_ai_analysis=services.brief_analysis_service is not None,
    )
    return {
        "brief_id": brief.payload["brief_id"],
        "session_id": brief.session_id,
        "revision": brief.revision,
        "status": brief.status,
    }
