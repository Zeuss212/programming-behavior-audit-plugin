"""Assessment configuration is hash-covered and immutable per plan version."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from classroom_sync.domain.schemas import ClassroomSchemaRegistry
from classroom_sync.models import Base
from classroom_sync.services.assessment_configs import AssessmentConfigService
from classroom_sync.services.plans import PlanDraftInput, PlanService
from tests.integration.test_plan_assignment_flow import profile_draft


def services() -> tuple[PlanService, AssessmentConfigService, datetime]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    root = Path(__file__).resolve().parents[4]
    schemas = ClassroomSchemaRegistry(root / "contracts" / "classroom" / "v1")
    now = datetime(2026, 9, 2, 6, 0, tzinfo=UTC)
    return (
        PlanService(session_factory, schemas, clock=lambda: now),
        AssessmentConfigService(session_factory, clock=lambda: now),
        now,
    )


def create_draft(plan_service: PlanService, now: datetime):
    return plan_service.create_draft(
        PlanDraftInput(
            space_id="space-1",
            parent_algorithm_id="parent-1",
            title="字典课堂练习",
            profile=profile_draft("学生是否正确读取字典中的值？"),
            scheduled_start_at=now,
            scheduled_end_at=now + timedelta(hours=1),
            ai_policy="prohibited",
        ),
        teacher_id="teacher-1",
    )


def test_saved_config_is_frozen_into_schema_v2_and_not_mutated_later() -> None:
    plan_service, config_service, now = services()
    draft = create_draft(plan_service, now)
    initial = config_service.get_or_create(draft.id, teacher_id="teacher-1")
    first_saved = config_service.update(
        draft.id,
        teacher_id="teacher-1",
        expected_draft_revision=initial.draft_revision,
        expected_config_revision=initial.config_revision,
        monitoring_scopes={**initial.monitoring_scopes, "paste_behavior": False},
        evaluation_dimensions=[
            {
                "id": "mastery",
                "name": "知识掌握",
                "description": "理解并正确应用知识点。",
                "weight_bps": 7000,
                "student_visible": True,
                "order": 1,
            },
            {
                "id": "debugging",
                "name": "调试能力",
                "description": "定位并修正错误。",
                "weight_bps": 3000,
                "student_visible": False,
                "order": 2,
            },
        ],
    )

    version_one = plan_service.publish_draft(draft.id, teacher_id="teacher-1")
    first_snapshot = dict(version_one.assessment_config or {})

    plan_service.update_draft(
        draft.id,
        profile=profile_draft("学生是否正确读取字典中的值？"),
        teacher_id="teacher-1",
        expected_revision=first_saved.draft_revision,
    )
    current = config_service.get_or_create(draft.id, teacher_id="teacher-1")
    config_service.update(
        draft.id,
        teacher_id="teacher-1",
        expected_draft_revision=current.draft_revision,
        expected_config_revision=current.config_revision,
        monitoring_scopes={**current.monitoring_scopes, "paste_behavior": True},
        evaluation_dimensions=current.evaluation_dimensions,
    )
    version_two = plan_service.publish_draft(draft.id, teacher_id="teacher-1")

    assert version_one.content_schema_version == 2
    assert first_snapshot == {
        "schema_version": 1,
        "monitoring_scopes": {
            "coding_process": True,
            "paste_behavior": False,
            "revision_process": True,
            "run_and_debug": True,
            "thinking_and_pause": True,
        },
        "evaluation_dimensions": first_saved.evaluation_dimensions,
        "total_bps": 10000,
    }
    assert version_one.assessment_config == first_snapshot
    assert version_two.assessment_config != first_snapshot
    assert version_two.content_hash != version_one.content_hash


def test_legacy_draft_without_config_keeps_schema_v1_publication() -> None:
    plan_service, _config_service, now = services()
    draft = create_draft(plan_service, now)

    published = plan_service.publish_draft(draft.id, teacher_id="teacher-1")

    assert published.content_schema_version == 1
    assert published.assessment_config is None
