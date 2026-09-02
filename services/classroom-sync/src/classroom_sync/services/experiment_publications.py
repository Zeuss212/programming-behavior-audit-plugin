"""Experiment creation context and idempotent assessment publication."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from classroom_sync.auth.fincolab import StudentChildExperiment
from classroom_sync.errors import NotFoundError, ValidationError
from classroom_sync.models import (
    ExperimentAssessmentConfig,
    ExperimentPublicationContext,
    PlanVersion,
)
from classroom_sync.repositories import ClassroomRepository
from classroom_sync.services.assignments import AssignmentService
from classroom_sync.services.plan_authoring import PlanAuthoringService
from classroom_sync.services.plans import PlanDraftInput, PlanService


@dataclass(frozen=True)
class ExperimentPublicationContextSnapshot:
    space_id: str
    parent_algorithm_id: str
    experiment_name: str
    statement: str
    scheduled_start_at: datetime
    scheduled_end_at: datetime
    ai_policy: str


@dataclass(frozen=True)
class AssessmentPublicationSnapshot:
    plan_version_id: str
    plan_id: str
    version: int
    assignment_count: int


class ExperimentPublicationService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        plan_service: PlanService,
        plan_authoring_service: PlanAuthoringService,
        assignment_service: AssignmentService,
        clock: Callable[[], datetime],
    ) -> None:
        self._session_factory = session_factory
        self._plan_service = plan_service
        self._plan_authoring_service = plan_authoring_service
        self._assignment_service = assignment_service
        self._clock = clock

    def upsert_context(
        self,
        *,
        space_id: str,
        parent_algorithm_id: str,
        experiment_name: str,
        statement: str,
        scheduled_start_at: datetime,
        scheduled_end_at: datetime,
        ai_policy: str,
        teacher_id: str,
    ) -> ExperimentPublicationContextSnapshot:
        values = self._normalize_context(
            experiment_name, statement, scheduled_start_at, scheduled_end_at, ai_policy
        )
        now = self._clock()
        with self._session_factory.begin() as session:
            repository = ClassroomRepository(session)
            repository.lock_plan_scope(space_id, parent_algorithm_id)
            context = self._find_context(session, space_id, parent_algorithm_id, for_update=True)
            if context is None:
                context = ExperimentPublicationContext(
                    id=str(uuid4()),
                    space_id=space_id,
                    parent_algorithm_id=parent_algorithm_id,
                    teacher_id=teacher_id,
                    created_at=now,
                    updated_at=now,
                    **values,
                )
                session.add(context)
            else:
                for key, value in values.items():
                    setattr(context, key, value)
                context.teacher_id = teacher_id
                context.updated_at = now
            session.flush()
            return self._context_snapshot(context)

    def publish_assessment(
        self,
        *,
        space_id: str,
        parent_algorithm_id: str,
        teacher_id: str,
        roster: Iterable[StudentChildExperiment],
    ) -> AssessmentPublicationSnapshot:
        roster_entries = tuple(roster)
        context, assessment, existing = self._publication_inputs(space_id, parent_algorithm_id)
        if existing is not None and self._matches(existing, context, assessment):
            assignments = self._assignment_service.sync_assignments(existing, roster_entries)
            return self._snapshot(existing, len(assignments))

        authoring = self._plan_authoring_service.create_or_return_open(
            teacher_id=teacher_id, space_id=space_id, parent_algorithm_id=parent_algorithm_id
        )
        draft = self._plan_service.create_draft(
            PlanDraftInput(
                authoring_session_id=authoring.authoring_session_id,
                space_id=space_id,
                parent_algorithm_id=parent_algorithm_id,
                title=context.experiment_name,
                profile=self._profile(context, assessment),
                scheduled_start_at=context.scheduled_start_at,
                scheduled_end_at=context.scheduled_end_at,
                ai_policy=context.ai_policy,
            ),
            teacher_id=teacher_id,
        )
        published = self._plan_service.publish_draft(draft.id, teacher_id=teacher_id)
        # The generated classroom profile uses schema v2.  PlanService only
        # closes authoring sessions for v3 drafts, so explicitly retire this
        # internal session to allow a later assessment edit to open the next
        # draft in the same plan series.
        self._plan_authoring_service.abandon(
            authoring.authoring_session_id,
            teacher_id=teacher_id,
        )
        assignments = self._assignment_service.sync_assignments(published, roster_entries)
        return self._snapshot(published, len(assignments))

    def _publication_inputs(
        self, space_id: str, parent_algorithm_id: str
    ) -> tuple[
        ExperimentPublicationContextSnapshot, ExperimentAssessmentConfig, PlanVersion | None
    ]:
        with self._session_factory.begin() as session:
            assessment = session.scalar(
                select(ExperimentAssessmentConfig).where(
                    ExperimentAssessmentConfig.space_id == space_id,
                    ExperimentAssessmentConfig.parent_algorithm_id == parent_algorithm_id,
                )
            )
            if assessment is None:
                raise NotFoundError("experiment_assessment_config_not_found")
            context = self._find_context(session, space_id, parent_algorithm_id)
            if context is None:
                context = self._backfill_context(assessment)
                session.add(context)
                session.flush()
            repository = ClassroomRepository(session)
            binding = repository.get_binding(space_id, parent_algorithm_id)
            existing = (
                repository.get_plan_version(binding.plan_id, binding.plan_version)
                if binding
                else None
            )
            return self._context_snapshot(context), assessment, existing

    def _backfill_context(
        self,
        assessment: ExperimentAssessmentConfig,
    ) -> ExperimentPublicationContext:
        """Create a safe first publication context for pre-migration experiments."""
        now = self._clock().astimezone(timezone.utc)
        created_at = self._stored_utc(assessment.created_at)
        start = max(now, created_at)
        return ExperimentPublicationContext(
            id=str(uuid4()),
            space_id=assessment.space_id,
            parent_algorithm_id=assessment.parent_algorithm_id,
            experiment_name=assessment.experiment_name,
            statement=(
                f"完成“{assessment.experiment_name}”实验，运行代码验证结果，并根据反馈修正实现。"
            ),
            scheduled_start_at=start,
            scheduled_end_at=start + timedelta(days=7),
            ai_policy="prohibited",
            teacher_id=assessment.teacher_id,
            created_at=now,
            updated_at=now,
        )

    @staticmethod
    def _find_context(
        session: Session, space_id: str, parent_algorithm_id: str, *, for_update: bool = False
    ) -> ExperimentPublicationContext | None:
        statement = select(ExperimentPublicationContext).where(
            ExperimentPublicationContext.space_id == space_id,
            ExperimentPublicationContext.parent_algorithm_id == parent_algorithm_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return session.scalar(statement)

    @classmethod
    def _context_snapshot(
        cls, context: ExperimentPublicationContext
    ) -> ExperimentPublicationContextSnapshot:
        return ExperimentPublicationContextSnapshot(
            space_id=context.space_id,
            parent_algorithm_id=context.parent_algorithm_id,
            experiment_name=context.experiment_name,
            statement=context.statement,
            scheduled_start_at=cls._stored_utc(context.scheduled_start_at),
            scheduled_end_at=cls._stored_utc(context.scheduled_end_at),
            ai_policy=context.ai_policy,
        )

    @staticmethod
    def _normalize_context(
        experiment_name: str,
        statement: str,
        scheduled_start_at: datetime,
        scheduled_end_at: datetime,
        ai_policy: str,
    ) -> dict[str, object]:
        name, text = experiment_name.strip(), statement.strip()
        if not name or len(name) > 200 or any(ord(char) < 32 for char in name):
            raise ValidationError("experiment_name_invalid")
        if not text or len(text) > 10_000 or any(ord(char) < 32 for char in text):
            raise ValidationError("experiment_statement_invalid")
        if scheduled_start_at.tzinfo is None or scheduled_start_at.utcoffset() is None:
            raise ValidationError("plan_schedule_timezone_required")
        if scheduled_end_at.tzinfo is None or scheduled_end_at.utcoffset() is None:
            raise ValidationError("plan_schedule_timezone_required")
        start, end = (
            scheduled_start_at.astimezone(timezone.utc),
            scheduled_end_at.astimezone(timezone.utc),
        )
        if end <= start:
            raise ValidationError("plan_schedule_invalid")
        if ai_policy not in {"prohibited", "allowed"}:
            raise ValidationError("plan_ai_policy_invalid")
        return {
            "experiment_name": name,
            "statement": text,
            "scheduled_start_at": start,
            "scheduled_end_at": end,
            "ai_policy": ai_policy,
        }

    @staticmethod
    def _stored_utc(value: datetime) -> datetime:
        return (
            value.replace(tzinfo=timezone.utc)
            if value.tzinfo is None
            else value.astimezone(timezone.utc)
        )

    @staticmethod
    def _assessment_snapshot(config: ExperimentAssessmentConfig) -> dict[str, object]:
        dimensions = deepcopy(config.evaluation_dimensions)
        return {
            "schema_version": config.schema_version,
            "monitoring_scopes": dict(config.monitoring_scopes),
            "evaluation_dimensions": dimensions,
            "total_bps": sum(int(item["weight_bps"]) for item in dimensions),
        }

    @classmethod
    def _matches(
        cls,
        plan: PlanVersion,
        context: ExperimentPublicationContextSnapshot,
        assessment: ExperimentAssessmentConfig,
    ) -> bool:
        profile_context = plan.profile.get("problem_context")
        return (
            plan.profile.get("title") == context.experiment_name
            and cls._stored_utc(plan.scheduled_start_at) == context.scheduled_start_at
            and cls._stored_utc(plan.scheduled_end_at) == context.scheduled_end_at
            and plan.ai_policy == context.ai_policy
            and isinstance(profile_context, Mapping)
            and profile_context.get("statement") == context.statement
            and plan.assessment_config == cls._assessment_snapshot(assessment)
        )

    @staticmethod
    def _snapshot(plan: PlanVersion, assignment_count: int) -> AssessmentPublicationSnapshot:
        return AssessmentPublicationSnapshot(plan.id, plan.plan_id, plan.version, assignment_count)

    @staticmethod
    def _profile(
        context: ExperimentPublicationContextSnapshot, assessment: ExperimentAssessmentConfig
    ) -> dict[str, object]:
        points: list[dict[str, object]] = []
        tests: list[dict[str, object]] = []
        dimensions: list[dict[str, object]] = []
        for order, item in enumerate(
            sorted(assessment.evaluation_dimensions, key=lambda value: int(value["order"]))
        ):
            point_id, test_id = f"KP_{order + 1:08d}", f"TEST_{order + 1:08d}"
            name, description = str(item["name"]), str(item["description"])
            points.append(
                {
                    "id": point_id,
                    "name": name,
                    "description": description,
                    "source": "teacher",
                    "order": order,
                }
            )
            tests.append(
                {
                    "id": test_id,
                    "name": f"{name} 基础验证",
                    "knowledge_point_ids": [point_id],
                    "kind": "function_call",
                    "input": "",
                    "expected": "",
                    "enabled": True,
                    "source": "teacher",
                    "order": order,
                }
            )
            dimensions.append(
                {
                    "knowledge_point_id": point_id,
                    "name": name,
                    "question": f"学生是否通过代码、运行和修改过程体现“{name}”？",
                    "no_known_exclusion": True,
                    "evidence_criteria": [
                        {
                            "id": f"{point_id}_SUPPORT",
                            "direction": "support",
                            "statement": description,
                        }
                    ],
                    "levels": [
                        {
                            "code": "possible",
                            "name": "初步掌握",
                            "definition": "出现了相关尝试，但证据仍不足以稳定判断。",
                        },
                        {
                            "code": "clear",
                            "name": "已掌握",
                            "definition": "代码、运行和验证过程形成了清晰的支持证据。",
                        },
                    ],
                    "teaching_actions": {
                        "possible": "通过追问与补充练习帮助学生巩固。",
                        "clear": "可进入下一知识点或提升难度。",
                    },
                    "analysis_config": {"mode": "llm_evidence"},
                }
            )
        return {
            "schema_version": 2,
            "problem_id": context.parent_algorithm_id,
            "title": context.experiment_name,
            "problem_context": {
                "statement": context.statement,
                "language": "python",
                "submission_contract": {"kind": "function", "entrypoint": "solve"},
            },
            "knowledge_points": points,
            "assessment_tests": tests,
            "confirmations": {"knowledge_points_hash": None, "tests_hash": None},
            "dimensions": dimensions,
        }
