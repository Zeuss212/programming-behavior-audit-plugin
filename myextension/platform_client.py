"""Small no-log HTTP client for the trusted classroom synchronization service."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
from typing import BinaryIO
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .platform_context_store import RegisteredPlatformContext

REQUEST_TIMEOUT_SECONDS = 10.0


class PlatformClientError(RuntimeError):
    """Stable client error whose message intentionally never includes request credentials."""


@dataclass(frozen=True)
class EvidenceUploadReceipt:
    """The idempotent receipt returned after the service stores one chunk."""

    evidence_id: str
    session_id: str
    sequence: int
    content_sha256: str


class PlatformSyncClient:
    """Exchange an ephemeral launch ticket without persisting or logging it."""

    def __init__(
        self,
        base_url: str,
        *,
        transport: Callable[..., BinaryIO] = urlopen,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._transport = transport

    def register(
        self, ticket: str, *, plugin_instance_id: str
    ) -> RegisteredPlatformContext:
        if not ticket.strip() or not plugin_instance_id.strip():
            raise PlatformClientError("platform_registration_invalid")
        request = Request(
            f"{self._base_url}/v1/classroom/plugin/sessions/register",
            data=json.dumps(
                {"ticket": ticket, "plugin_instance_id": plugin_instance_id}
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self._transport(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                raw = response.read()
        except HTTPError as error:
            if error.code in {401, 403}:
                raise PlatformClientError("platform_registration_unauthorized") from error
            if error.code == 409:
                raise PlatformClientError("platform_registration_conflict") from error
            if 400 <= error.code < 500:
                raise PlatformClientError("platform_registration_invalid") from error
            raise PlatformClientError("platform_registration_failed") from error
        except (OSError, URLError) as error:
            raise PlatformClientError("platform_registration_unavailable") from error
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as error:
            raise PlatformClientError("platform_registration_invalid_response") from error
        if not isinstance(payload, dict):
            raise PlatformClientError("platform_registration_invalid_response")
        try:
            return self._registered_context(payload)
        except (KeyError, ValueError) as error:
            raise PlatformClientError("platform_registration_invalid_response") from error

    def refresh(self, context: RegisteredPlatformContext) -> RegisteredPlatformContext:
        """Refresh a persisted session without returning its token to the browser."""

        request = Request(
            f"{self._base_url}/v1/classroom/plugin/sessions/{context.session_id}/context/refresh",
            headers={"Authorization": f"Bearer {context.access_token}"},
            method="POST",
        )
        try:
            with self._transport(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                raw = response.read()
        except HTTPError as error:
            if error.code in {401, 403}:
                raise PlatformClientError("platform_context_refresh_unauthorized") from error
            if error.code == 409:
                raise PlatformClientError("platform_context_refresh_conflict") from error
            if 400 <= error.code < 500:
                raise PlatformClientError("platform_context_refresh_invalid") from error
            raise PlatformClientError("platform_context_refresh_failed") from error
        except (OSError, URLError) as error:
            raise PlatformClientError("platform_context_refresh_unavailable") from error
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as error:
            raise PlatformClientError("platform_context_refresh_invalid_response") from error
        if not isinstance(payload, dict):
            raise PlatformClientError("platform_context_refresh_invalid_response")
        try:
            return self._registered_context(payload)
        except (KeyError, ValueError) as error:
            raise PlatformClientError("platform_context_refresh_invalid_response") from error

    def upload_evidence(
        self,
        context: RegisteredPlatformContext,
        *,
        sequence: int,
        body: bytes,
        first_event_sequence: int,
        last_event_sequence: int,
    ) -> EvidenceUploadReceipt:
        """Upload one gzip evidence chunk using the private plugin token."""

        if (
            not isinstance(sequence, int)
            or isinstance(sequence, bool)
            or sequence < 1
            or not isinstance(first_event_sequence, int)
            or isinstance(first_event_sequence, bool)
            or first_event_sequence < 1
            or not isinstance(last_event_sequence, int)
            or isinstance(last_event_sequence, bool)
            or last_event_sequence < first_event_sequence
            or not isinstance(body, bytes)
            or not body
        ):
            raise PlatformClientError("platform_evidence_invalid")
        request = Request(
            f"{self._base_url}/v1/classroom/plugin/sessions/{context.session_id}/evidence/{sequence}",
            data=body,
            headers={
                "Authorization": f"Bearer {context.access_token}",
                "Content-Type": "application/gzip",
                "X-First-Event-Sequence": str(first_event_sequence),
                "X-Last-Event-Sequence": str(last_event_sequence),
            },
            method="PUT",
        )
        try:
            with self._transport(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                raw = response.read()
        except HTTPError as error:
            if error.code in {401, 403}:
                raise PlatformClientError("platform_evidence_unauthorized") from error
            if error.code == 409:
                raise PlatformClientError("platform_evidence_conflict") from error
            if 400 <= error.code < 500:
                raise PlatformClientError("platform_evidence_invalid") from error
            raise PlatformClientError("platform_evidence_failed") from error
        except (OSError, URLError) as error:
            raise PlatformClientError("platform_evidence_unavailable") from error
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as error:
            raise PlatformClientError("platform_evidence_invalid_response") from error
        if not isinstance(payload, dict):
            raise PlatformClientError("platform_evidence_invalid_response")
        expected_hash = sha256(body).hexdigest()
        if (
            not isinstance(payload.get("evidence_id"), str)
            or not payload["evidence_id"].strip()
            or payload.get("session_id") != context.session_id
            or payload.get("sequence") != sequence
            or payload.get("content_sha256") != expected_hash
        ):
            raise PlatformClientError("platform_evidence_invalid_response")
        return EvidenceUploadReceipt(
            evidence_id=payload["evidence_id"],
            session_id=context.session_id,
            sequence=sequence,
            content_sha256=expected_hash,
        )

    @staticmethod
    def _registered_context(payload: dict[str, object]) -> RegisteredPlatformContext:
        return RegisteredPlatformContext.from_dict(
            {
                "assignment_id": payload["assignment_id"],
                "plan_id": payload["plan_id"],
                "plan_version": payload["plan_version"],
                "session_id": payload["session_id"],
                "access_token": payload["access_token"],
                "access_token_expires_at": payload["expires_at"],
                "profile": payload["profile"],
                "scheduled_end_at": payload["scheduled_end_at"],
                "evidence_cutoff_at": payload["evidence_cutoff_at"],
                "last_sync_at": payload["last_sync_at"],
            }
        )
