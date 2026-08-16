"""Runtime configuration for the classroom synchronization service."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Settings:
    """Explicit service configuration with a health-check-safe subset.

    ``database_url`` stays optional for the process-level liveness/readiness
    tests.  Serving business routes is stricter: callers must explicitly
    validate every dependency with :meth:`require_runtime_dependencies`.
    """

    database_url: str | None
    s3_endpoint_url: str | None = None
    s3_bucket: str | None = None
    s3_access_key: str | None = field(default=None, repr=False)
    s3_secret_key: str | None = field(default=None, repr=False)
    fincolab_base_url: str | None = None
    fincolab_organization_id: str | None = None
    plugin_jwt_secret: str | None = field(default=None, repr=False)
    ai_base_url: str | None = None
    ai_model: str | None = None
    ai_api_key: str | None = field(default=None, repr=False)
    ai_timeout_seconds: int | None = 15
    ai_max_attempts: int = 3

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        values = os.environ if env is None else env
        database_url = cls._optional(values, "CLASSROOM_DATABASE_URL")
        if database_url is None:
            raise RuntimeError("CLASSROOM_DATABASE_URL must be configured.")
        return cls(
            database_url=database_url,
            s3_endpoint_url=cls._optional(values, "CLASSROOM_S3_ENDPOINT_URL"),
            s3_bucket=cls._optional(values, "CLASSROOM_S3_BUCKET"),
            s3_access_key=cls._optional(values, "CLASSROOM_S3_ACCESS_KEY"),
            s3_secret_key=cls._optional(values, "CLASSROOM_S3_SECRET_KEY"),
            fincolab_base_url=cls._optional(values, "CLASSROOM_FINCOLAB_BASE_URL"),
            fincolab_organization_id=cls._optional(
                values, "CLASSROOM_FINCOLAB_ORGANIZATION_ID"
            ),
            plugin_jwt_secret=cls._optional(values, "CLASSROOM_PLUGIN_JWT_SECRET"),
            ai_base_url=cls._optional(values, "CLASSROOM_AI_BASE_URL"),
            ai_model=cls._optional(values, "CLASSROOM_AI_MODEL"),
            ai_api_key=cls._optional(values, "CLASSROOM_AI_API_KEY"),
            ai_timeout_seconds=cls._optional_integer(
                values, "CLASSROOM_AI_TIMEOUT_SECONDS", default=15
            ),
            ai_max_attempts=cls._bounded_integer(
                values, "CLASSROOM_AI_MAX_ATTEMPTS", default=3, minimum=1, maximum=3
            ),
        )

    def require_runtime_dependencies(self) -> None:
        """Reject partial configuration before the HTTP service accepts traffic."""

        for name, value in (
            ("CLASSROOM_DATABASE_URL", self.database_url),
            ("CLASSROOM_S3_ENDPOINT_URL", self.s3_endpoint_url),
            ("CLASSROOM_S3_BUCKET", self.s3_bucket),
            ("CLASSROOM_S3_ACCESS_KEY", self.s3_access_key),
            ("CLASSROOM_S3_SECRET_KEY", self.s3_secret_key),
            ("CLASSROOM_FINCOLAB_BASE_URL", self.fincolab_base_url),
            ("CLASSROOM_FINCOLAB_ORGANIZATION_ID", self.fincolab_organization_id),
            ("CLASSROOM_PLUGIN_JWT_SECRET", self.plugin_jwt_secret),
        ):
            if value is None:
                raise RuntimeError(f"{name} must be configured.")
        if self.plugin_jwt_secret is None or len(self.plugin_jwt_secret) < 32:
            raise RuntimeError("CLASSROOM_PLUGIN_JWT_SECRET must contain at least 32 characters.")

    @staticmethod
    def _optional(values: Mapping[str, str], name: str) -> str | None:
        return values.get(name, "").strip() or None

    @staticmethod
    def _optional_integer(
        values: Mapping[str, str], name: str, *, default: int
    ) -> int | None:
        value = Settings._optional(values, name)
        if value is None:
            return default
        try:
            return int(value)
        except ValueError:
            return None

    @staticmethod
    def _bounded_integer(
        values: Mapping[str, str], name: str, *, default: int, minimum: int, maximum: int
    ) -> int:
        value = Settings._optional(values, name)
        if value is None:
            return default
        try:
            parsed = int(value)
        except ValueError as error:
            raise RuntimeError(f"{name} must be an integer between {minimum} and {maximum}.") from error
        if not minimum <= parsed <= maximum:
            raise RuntimeError(f"{name} must be an integer between {minimum} and {maximum}.")
        return parsed
