"""Strict runtime configuration for local and classroom-student plugin modes."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from .behavior_log_store import resolve_log_root

MODE_ENV = "JUPYTERLAB_BEHAVIOR_AUDIT_PLATFORM_MODE"
SYNC_BASE_URL_ENV = "JUPYTERLAB_BEHAVIOR_AUDIT_SYNC_BASE_URL"
LOG_DIR_ENV = "JUPYTERLAB_BEHAVIOR_AUDIT_LOG_DIR"
DEADLINE_POLL_SECONDS_ENV = "JUPYTERLAB_BEHAVIOR_AUDIT_DEADLINE_POLL_SECONDS"
ALLOW_INSECURE_LOOPBACK_ENV = "JUPYTERLAB_BEHAVIOR_AUDIT_ALLOW_INSECURE_LOOPBACK"


@dataclass(frozen=True)
class PlatformConfig:
    mode: str
    sync_base_url: str | None
    log_root: Path
    deadline_poll_seconds: int

    @property
    def student_mode(self) -> bool:
        return self.mode == "student"

    def capabilities(self) -> dict[str, bool]:
        """Return server-authoritative capabilities for the configured runtime mode."""

        if self.student_mode:
            return {
                "canAuthorPlan": False,
                "canPublishPlan": False,
                "canConfigureAi": False,
                "canUseAssessmentAssist": False,
                "canCapture": True,
                "canSubmit": True,
            }
        return {
            "canAuthorPlan": True,
            "canPublishPlan": True,
            "canConfigureAi": True,
            "canUseAssessmentAssist": True,
            "canCapture": True,
            "canSubmit": True,
        }

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> PlatformConfig:
        values = os.environ if env is None else env
        mode = values.get(MODE_ENV, "local").strip().lower() or "local"
        if mode not in {"local", "student"}:
            raise RuntimeError("platform mode must be either 'local' or 'student'.")
        sync_base_url = values.get(SYNC_BASE_URL_ENV, "").strip() or None
        allow_insecure_loopback = values.get(ALLOW_INSECURE_LOOPBACK_ENV, "").lower() == "true"
        if mode == "student":
            if sync_base_url is None:
                raise RuntimeError("student mode requires sync_base_url.")
            cls._validate_sync_base_url(sync_base_url, allow_insecure_loopback)
        log_root = Path(values.get(LOG_DIR_ENV, str(resolve_log_root()))).expanduser()
        deadline_poll_seconds = cls._parse_poll_seconds(values.get(DEADLINE_POLL_SECONDS_ENV, "30"))
        return cls(
            mode=mode,
            sync_base_url=sync_base_url,
            log_root=log_root,
            deadline_poll_seconds=deadline_poll_seconds,
        )

    @staticmethod
    def _validate_sync_base_url(value: str, allow_insecure_loopback: bool) -> None:
        parsed = urlparse(value)
        if parsed.scheme == "https" and parsed.hostname:
            return
        loopback_hosts = {"127.0.0.1", "localhost", "::1"}
        if (
            allow_insecure_loopback
            and parsed.scheme == "http"
            and parsed.hostname in loopback_hosts
        ):
            return
        raise RuntimeError("student mode sync_base_url must use HTTPS.")

    @staticmethod
    def _parse_poll_seconds(value: str) -> int:
        try:
            seconds = int(value)
        except ValueError as error:
            raise RuntimeError("deadline poll seconds must be an integer.") from error
        if not 5 <= seconds <= 300:
            raise RuntimeError("deadline poll seconds must be between 5 and 300.")
        return seconds
