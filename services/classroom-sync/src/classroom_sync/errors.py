"""Stable domain errors that routers can map without exposing internal details."""

from __future__ import annotations


class ClassroomServiceError(Exception):
    """A client-safe failure with a stable code and retry contract."""

    status_code = 500
    retryable = False

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class AuthenticationError(ClassroomServiceError):
    status_code = 401


class AuthorizationError(ClassroomServiceError):
    status_code = 403


class NotFoundError(ClassroomServiceError):
    status_code = 404


class RosterConflictError(ClassroomServiceError):
    status_code = 409


class UpstreamContractError(ClassroomServiceError):
    status_code = 503
    retryable = True


class UpstreamUnavailableError(ClassroomServiceError):
    status_code = 503
    retryable = True
