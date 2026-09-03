"""Draft-owned independent assessment dimensions with strict optimistic locking."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from typing import cast
from uuid import uuid4

from sqlalchemy.orm import Session, sessionmaker

from classroom_sync.errors import (
    AuthorizationError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from classroom_sync.models import AssessmentConfig, AuditEvent, PlanDraft
from classroom_sync.repositories import ClassroomRepository

SCHEMA_VERSION = 1
SCOPE_KEYS = frozenset(
    {
        "coding_process",
        "revision_process",
        "run_and_debug",
        "thinking_and_pause",
        "paste_behavior",
    }
)
DIMENSION_KEYS = frozenset(
    {"id", "name", "description", "weight_bps", "student_visible", "order"}
)
DIMENSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")

DEFAULT_MONITORING_SCOPES = {
    "coding_process": True,
    "revision_process": True,
    "run_and_debug": True,
    "thinking_and_pause": True,
    "paste_behavior": True,
}
DEFAULT_EVALUATION_DIMENSIONS: list[dict[str, object]] = [
    {
        "id": "knowledge_mastery",
        "name": "知识点掌握",
        "description": "对本实验知识点的理解、应用与薄弱点表现。",
        "weight_bps": 3000,
        "student_visible": True,
        "order": 1,
    },
    {
        "id": "debugging_ability",
        "name": "调试能力",
        "description": "运行失败后的错误识别、定位和修正过程。",
        "weight_bps": 2500,
        "student_visible": True,
        "order": 2,
    },
    {
        "id": "problem_solving",
        "name": "问题解决",
        "description": "分析问题、选择方案并完成实现的过程。",
        "weight_bps": 2000,
        "student_visible": True,
        "order": 3,
    },
    {
        "id": "learning_process",
        "name": "学习过程",
        "description": "持续尝试、验证假设并根据反馈调整的表现。",
        "weight_bps": 1500,
        "student_visible": True,
        "order": 4,
    },
    {
        "id": "coding_habits",
        "name": "编程规范",
        "description": "代码组织、命名、可读性与基本工程习惯。",
        "weight_bps": 1000,
        "student_visible": True,
        "order": 5,
    },
]


@dataclass(frozen=True)
class AssessmentConfigSnapshot:
    draft_id: str
    draft_revision: int
    config_revision: int
    schema_version: int
    monitoring_scopes: dict[str, bool]
    evaluation_dimensions: list[dict[str, object]]

    @property
    def total_bps(self) -> int:
        return sum(cast(int, item["weight_bps"]) for item in self.evaluation_dimensions)


class AssessmentConfigService:
    """Create, read and update one assessment config inside database transactions."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        clock: Callable[[], datetime],
    ) -> None:
        self._session_factory = session_factory
        self._clock = clock

    def get_or_create(
        self, draft_id: str, *, teacher_id: str
    ) -> AssessmentConfigSnapshot:
        now = self._clock()
        with self._session_factory.begin() as session:
            repository = ClassroomRepository(session)
            draft = self._owned_draft(repository, draft_id, teacher_id=teacher_id)
            config = repository.get_assessment_config(draft_id, for_update=True)
            if config is None:
                config = AssessmentConfig(
                    draft_id=draft.id,
                    schema_version=SCHEMA_VERSION,
                    config_revision=0,
                    monitoring_scopes=deepcopy(DEFAULT_MONITORING_SCOPES),
                    evaluation_dimensions=deepcopy(DEFAULT_EVALUATION_DIMENSIONS),
                    created_at=now,
                    updated_at=now,
                )
                session.add(config)
                session.flush()
            return self._snapshot(draft, config)

    def update(
        self,
        draft_id: str,
        *,
        teacher_id: str,
        expected_draft_revision: int,
        expected_config_revision: int,
        monitoring_scopes: Mapping[str, object],
        evaluation_dimensions: Sequence[Mapping[str, object]],
    ) -> AssessmentConfigSnapshot:
        normalized_scopes = self._normalize_scopes(monitoring_scopes)
        normalized_dimensions = self._normalize_dimensions(evaluation_dimensions)
        now = self._clock()
        with self._session_factory.begin() as session:
            repository = ClassroomRepository(session)
            draft = self._owned_draft(
                repository,
                draft_id,
                teacher_id=teacher_id,
                for_update=True,
            )
            config = repository.get_assessment_config(draft_id, for_update=True)
            if config is None:
                raise NotFoundError("assessment_config_missing")
            if (
                draft.revision != expected_draft_revision
                or config.config_revision != expected_config_revision
            ):
                raise ConflictError(
                    "assessment_config_stale",
                    details={
                        "draft_revision": draft.revision,
                        "config_revision": config.config_revision,
                    },
                )
            config.monitoring_scopes = normalized_scopes
            config.evaluation_dimensions = normalized_dimensions
            config.config_revision += 1
            config.updated_at = now
            draft.revision += 1
            draft.updated_at = now
            session.add(
                AuditEvent(
                    id=str(uuid4()),
                    actor_id=teacher_id,
                    event_type="assessment_config_updated",
                    entity_type="assessment_config",
                    entity_id=draft.id,
                    request_id=None,
                    payload={"config_revision": config.config_revision},
                    created_at=now,
                )
            )
            session.flush()
            return self._snapshot(draft, config)

    @staticmethod
    def _owned_draft(
        repository: ClassroomRepository,
        draft_id: str,
        *,
        teacher_id: str,
        for_update: bool = False,
    ) -> PlanDraft:
        draft = repository.get_plan_draft(draft_id, for_update=for_update)
        if draft is None:
            raise NotFoundError("plan_draft_not_found")
        if draft.teacher_id != teacher_id:
            raise AuthorizationError("plan_draft_owner_mismatch")
        if draft.published_revision == draft.revision:
            raise ConflictError("assessment_draft_not_editable")
        return draft

    @staticmethod
    def _normalize_scopes(value: Mapping[str, object]) -> dict[str, bool]:
        if set(value) != SCOPE_KEYS or any(type(value[key]) is not bool for key in SCOPE_KEYS):
            raise ValidationError("assessment_config_invalid")
        return {key: cast(bool, value[key]) for key in sorted(SCOPE_KEYS)}

    @classmethod
    def _normalize_dimensions(
        cls, values: Sequence[Mapping[str, object]]
    ) -> list[dict[str, object]]:
        if isinstance(values, (str, bytes)) or not 1 <= len(values) <= 10:
            raise ValidationError("assessment_config_invalid")
        normalized: list[dict[str, object]] = []
        ids: set[str] = set()
        orders: set[int] = set()
        for raw in values:
            if set(raw) != DIMENSION_KEYS:
                raise ValidationError("assessment_config_invalid")
            dimension_id = raw["id"]
            name = raw["name"]
            description = raw["description"]
            weight_bps = raw["weight_bps"]
            student_visible = raw["student_visible"]
            order = raw["order"]
            if (
                not isinstance(dimension_id, str)
                or DIMENSION_ID_PATTERN.fullmatch(dimension_id) is None
                or not isinstance(name, str)
                or not 1 <= len(name.strip()) <= 50
                or not isinstance(description, str)
                or len(description.strip()) > 500
                or type(weight_bps) is not int
                or not 1 <= weight_bps <= 10000
                or type(student_visible) is not bool
                or type(order) is not int
                or order < 1
            ):
                raise ValidationError("assessment_config_invalid")
            if dimension_id in ids or order in orders:
                raise ValidationError("assessment_dimension_duplicate")
            ids.add(dimension_id)
            orders.add(order)
            normalized.append(
                {
                    "id": dimension_id,
                    "name": name.strip(),
                    "description": description.strip(),
                    "weight_bps": weight_bps,
                    "student_visible": student_visible,
                    "order": order,
                }
            )
        if sum(cast(int, item["weight_bps"]) for item in normalized) != 10000:
            raise ValidationError("assessment_weight_total_invalid")
        return sorted(normalized, key=lambda item: cast(int, item["order"]))

    @staticmethod
    def _snapshot(
        draft: PlanDraft, config: AssessmentConfig
    ) -> AssessmentConfigSnapshot:
        return AssessmentConfigSnapshot(
            draft_id=draft.id,
            draft_revision=draft.revision,
            config_revision=config.config_revision,
            schema_version=config.schema_version,
            monitoring_scopes=dict(config.monitoring_scopes),
            evaluation_dimensions=deepcopy(config.evaluation_dimensions),
        )
