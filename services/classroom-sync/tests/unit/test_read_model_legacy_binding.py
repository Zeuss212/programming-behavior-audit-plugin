"""Compatibility coverage for plans published before atomic experiment bindings."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from classroom_sync.models import Base, PlanVersion
from classroom_sync.services.read_models import ClassroomReadService


def test_experiment_plan_falls_back_to_latest_legacy_published_version_without_binding() -> None:
    """Removing the legacy fallback would strand already-published classrooms in analytics."""

    now = datetime(2026, 9, 2, 9, 0, tzinfo=UTC)
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory.begin() as session:
        session.add(PlanVersion(
            id="version-2",
            plan_id="plan-1",
            profile_id="profile-1",
            version=2,
            source_draft_revision=1,
            space_id="space-1",
            parent_algorithm_id="parent-1",
            profile={"title": "历史课堂计划"},
            content_hash="a" * 64,
            scheduled_start_at=now,
            scheduled_end_at=now + timedelta(hours=1),
            ai_policy="prohibited",
            published_at=now,
            teacher_id="teacher-1",
        ))

    summary = ClassroomReadService(factory).get_experiment_plan("space-1", "parent-1")

    assert summary["plan_version_id"] == "version-2"
    assert summary["title"] == "历史课堂计划"
