"""Failure-isolated automatic training-record refresh coordination."""

from __future__ import annotations

import logging

from .session_log_service import SessionLogService


class TrainingRecordRefresher:
    """Refresh a session export without disturbing its durable job state."""

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
            self._service.export_training_record(session_id)
        except Exception:
            self._logger.warning("training_record_refresh_failed")
            return False
        return True
