"""Failure-isolated automatic classroom-brief refresh coordination."""

from __future__ import annotations

import logging

from .session_log_service import SessionLogService


class ClassroomBriefRefresher:
    """Refresh a classroom brief without disturbing durable session state."""

    def __init__(
        self,
        service: SessionLogService,
        *,
        logger: logging.Logger,
    ) -> None:
        self._service = service
        self._logger = logger

    def refresh(self, session_id: str) -> bool:
        try:
            self._service.export_classroom_brief(session_id)
        except Exception:
            self._logger.warning("classroom_brief_refresh_failed")
            return False
        return True
