"""Stable domain errors that routers can map without exposing internal details."""

from __future__ import annotations

import json
from collections.abc import Mapping

_MAX_SAFE_ERROR_DETAILS_BYTES = 32_768


class ClassroomServiceError(Exception):
    """A client-safe failure with a stable code and retry contract."""

    status_code = 500
    retryable = False

    def __init__(
        self,
        code: str,
        *,
        details: Mapping[str, object] | None = None,
    ) -> None:
        self.code = code
        self.details = self._bounded_details(details)
        super().__init__(code)

    @staticmethod
    def _bounded_details(
        details: Mapping[str, object] | None,
    ) -> dict[str, object] | None:
        if details is None:
            return None
        encoded = json.dumps(
            dict(details),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
        if len(encoded.encode("utf-8")) > _MAX_SAFE_ERROR_DETAILS_BYTES:
            raise ValueError("classroom error details exceed the safe bound")
        decoded = json.loads(encoded)
        if not isinstance(decoded, dict):
            raise TypeError("classroom error details must be an object")
        return decoded


class AuthenticationError(ClassroomServiceError):
    status_code = 401


class AuthorizationError(ClassroomServiceError):
    status_code = 403


class ConflictError(ClassroomServiceError):
    status_code = 409


class PublicationGateBlockedError(ClassroomServiceError):
    """A deterministic, non-retryable publication decision."""

    status_code = 409

    def __init__(self, details: Mapping[str, object]) -> None:
        super().__init__("publication_gate_blocked", details=details)


class NotFoundError(ClassroomServiceError):
    status_code = 404


class RosterConflictError(ClassroomServiceError):
    status_code = 409


class UpstreamContractError(ClassroomServiceError):
    status_code = 503
    retryable = True


class ValidationError(ClassroomServiceError):
    status_code = 422


class UpstreamUnavailableError(ClassroomServiceError):
    status_code = 503
    retryable = True

    def __init__(self, code: str, *, retryable: bool = True) -> None:
        super().__init__(code)
        self.retryable = retryable


class AiSuggestionUnavailableError(ClassroomServiceError):
    """The optional AI drafting facility is not configured for this classroom."""

    status_code = 503
