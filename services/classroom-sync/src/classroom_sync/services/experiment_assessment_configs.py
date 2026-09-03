"""Assessment configuration persisted independently of classroom plans."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from typing import cast
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from classroom_sync.errors import ConflictError, NotFoundError, ValidationError
from classroom_sync.models import AuditEvent, ExperimentAssessmentConfig
from classroom_sync.repositories import ClassroomRepository
from classroom_sync.services.assessment_configs import (
    DEFAULT_EVALUATION_DIMENSIONS,
    DEFAULT_MONITORING_SCOPES,
    SCHEMA_VERSION,
    AssessmentConfigService,
)


@dataclass(frozen=True)
class ExperimentAssessmentConfigSnapshot:
    space_id: str
    parent_algorithm_id: str
    experiment_name: str
    config_revision: int
    schema_version: int
    monitoring_scopes: dict[str, bool]
    evaluation_dimensions: list[dict[str, object]]

    @property
    def total_bps(self) -> int:
        return sum(cast(int, item["weight_bps"]) for item in self.evaluation_dimensions)


class ExperimentAssessmentConfigService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        clock: Callable[[], datetime],
    ) -> None:
        self._session_factory = session_factory
        self._clock = clock

    def ensure(
        self,
        *,
        space_id: str,
        parent_algorithm_id: str,
        experiment_name: str,
        teacher_id: str,
    ) -> ExperimentAssessmentConfigSnapshot:
        normalized_name = self._normalize_name(experiment_name)
        now = self._clock()
        with self._session_factory.begin() as session:
            ClassroomRepository(session).lock_plan_scope(space_id, parent_algorithm_id)
            config = self._find(session, space_id, parent_algorithm_id, for_update=True)
            if config is None:
                config = ExperimentAssessmentConfig(
                    id=str(uuid4()),
                    space_id=space_id,
                    parent_algorithm_id=parent_algorithm_id,
                    experiment_name=normalized_name,
                    teacher_id=teacher_id,
                    schema_version=SCHEMA_VERSION,
                    config_revision=0,
                    monitoring_scopes=deepcopy(DEFAULT_MONITORING_SCOPES),
                    evaluation_dimensions=deepcopy(DEFAULT_EVALUATION_DIMENSIONS),
                    created_at=now,
                    updated_at=now,
                )
                session.add(config)
                self._audit(session, teacher_id, "experiment_assessment_config_created", config.id, now)
                session.flush()
            elif config.experiment_name != normalized_name:
                config.experiment_name = normalized_name
                config.updated_at = now
            return self._snapshot(config)

    def get(
        self, *, space_id: str, parent_algorithm_id: str
    ) -> ExperimentAssessmentConfigSnapshot:
        with self._session_factory() as session:
            config = self._find(session, space_id, parent_algorithm_id)
            if config is None:
                raise NotFoundError("experiment_assessment_config_not_found")
            return self._snapshot(config)

    def update(
        self,
        *,
        space_id: str,
        parent_algorithm_id: str,
        experiment_name: str,
        teacher_id: str,
        expected_config_revision: int,
        monitoring_scopes: Mapping[str, object],
        evaluation_dimensions: Sequence[Mapping[str, object]],
    ) -> ExperimentAssessmentConfigSnapshot:
        normalized_name = self._normalize_name(experiment_name)
        normalized_scopes = AssessmentConfigService._normalize_scopes(monitoring_scopes)
        normalized_dimensions = AssessmentConfigService._normalize_dimensions(
            evaluation_dimensions
        )
        now = self._clock()
        with self._session_factory.begin() as session:
            ClassroomRepository(session).lock_plan_scope(space_id, parent_algorithm_id)
            config = self._find(session, space_id, parent_algorithm_id, for_update=True)
            if config is None:
                raise NotFoundError("experiment_assessment_config_not_found")
            if config.config_revision != expected_config_revision:
                raise ConflictError(
                    "assessment_config_stale",
                    details={"config_revision": config.config_revision},
                )
            config.experiment_name = normalized_name
            config.monitoring_scopes = normalized_scopes
            config.evaluation_dimensions = normalized_dimensions
            config.config_revision += 1
            config.updated_at = now
            self._audit(session, teacher_id, "experiment_assessment_config_updated", config.id, now)
            session.flush()
            return self._snapshot(config)

    @staticmethod
    def _normalize_name(value: str) -> str:
        normalized = value.strip()
        if not normalized or len(normalized) > 200 or any(ord(char) < 32 for char in normalized):
            raise ValidationError("experiment_name_invalid")
        return normalized

    @staticmethod
    def _find(
        session: Session,
        space_id: str,
        parent_algorithm_id: str,
        *,
        for_update: bool = False,
    ) -> ExperimentAssessmentConfig | None:
        statement = select(ExperimentAssessmentConfig).where(
            ExperimentAssessmentConfig.space_id == space_id,
            ExperimentAssessmentConfig.parent_algorithm_id == parent_algorithm_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return session.scalar(statement)

    @staticmethod
    def _snapshot(config: ExperimentAssessmentConfig) -> ExperimentAssessmentConfigSnapshot:
        return ExperimentAssessmentConfigSnapshot(
            space_id=config.space_id,
            parent_algorithm_id=config.parent_algorithm_id,
            experiment_name=config.experiment_name,
            config_revision=config.config_revision,
            schema_version=config.schema_version,
            monitoring_scopes=dict(config.monitoring_scopes),
            evaluation_dimensions=deepcopy(config.evaluation_dimensions),
        )

    @staticmethod
    def _audit(
        session: Session,
        teacher_id: str,
        event_type: str,
        config_id: str,
        created_at: datetime,
    ) -> None:
        session.add(
            AuditEvent(
                id=str(uuid4()),
                actor_id=teacher_id,
                event_type=event_type,
                entity_type="experiment_assessment_config",
                entity_id=config_id,
                request_id=None,
                payload={},
                created_at=created_at,
            )
        )
