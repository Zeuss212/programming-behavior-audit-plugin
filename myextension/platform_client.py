"""Small no-log HTTP client for the trusted classroom synchronization service."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import BinaryIO
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .platform_context_store import RegisteredPlatformContext

REQUEST_TIMEOUT_SECONDS = 10.0


class PlatformClientError(RuntimeError):
    """Stable client error whose message intentionally never includes request credentials."""


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
            return RegisteredPlatformContext.from_dict(
                {
                    "assignment_id": payload["assignment_id"],
                    "plan_id": payload["plan_id"],
                    "plan_version": payload["plan_version"],
                    "session_id": payload["session_id"],
                    "access_token": payload["access_token"],
                    "access_token_expires_at": payload["expires_at"],
                    "evidence_cutoff_at": payload["evidence_cutoff_at"],
                }
            )
        except (KeyError, ValueError) as error:
            raise PlatformClientError("platform_registration_invalid_response") from error
