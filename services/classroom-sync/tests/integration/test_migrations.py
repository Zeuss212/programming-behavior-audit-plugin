from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from classroom_sync.models import (
    Base,
    ClassroomBriefAnalysisJob,
    ClassroomPlanSuggestionJob,
    StudentBrief,
)

CORE_TABLES = {
    "plan_drafts",
    "plan_versions",
    "experiment_plan_bindings",
    "student_assignments",
    "classroom_tickets",
    "monitor_sessions",
    "evidence_chunks",
    "student_briefs",
    "teacher_reviews",
    "classroom_deadline_jobs",
    "classroom_brief_analysis_jobs",
    "classroom_plan_suggestion_jobs",
    "audit_events",
}


def migration_config(database_url: str) -> Config:
    service_root = Path(__file__).resolve().parents[2]
    config = Config(str(service_root / "alembic.ini"))
    config.set_main_option("script_location", str(service_root / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def test_migration_revision_ids_fit_alembic_default_version_column():
    """PostgreSQL's default alembic_version column permits at most 32 characters."""
    scripts = ScriptDirectory.from_config(migration_config("sqlite://"))
    assert all(len(script.revision) <= 32 for script in scripts.walk_revisions())


def test_core_migration_round_trip_and_uniqueness(tmp_path: Path):
    """Core classroom state survives upgrade/downgrade and enforces idempotency keys."""
    database_url = f"sqlite:///{tmp_path / 'classroom.db'}"
    config = migration_config(database_url)

    command.upgrade(config, "head")
    engine = create_engine(database_url)
    assert CORE_TABLES <= set(Base.metadata.tables)
    assert CORE_TABLES <= set(inspect(engine).get_table_names())

    command.downgrade(config, "base")
    assert CORE_TABLES.isdisjoint(inspect(engine).get_table_names())
    command.upgrade(config, "head")
    assert CORE_TABLES <= set(inspect(engine).get_table_names())

    now = datetime(2026, 8, 12, 8, 0, tzinfo=UTC)
    tables = Base.metadata.tables
    with engine.begin() as connection:
        connection.execute(
            tables["plan_versions"].insert(),
            {
                "id": "plan-version-1",
                "plan_id": "plan-1",
                "profile_id": "profile-1",
                "version": 1,
                "source_draft_revision": 0,
                "space_id": "space-1",
                "parent_algorithm_id": "parent-1",
                "profile": {"schema_version": 2},
                "content_hash": "a" * 64,
                "scheduled_start_at": now,
                "scheduled_end_at": now,
                "ai_policy": "prohibited",
                "published_at": now,
                "teacher_id": "teacher-1",
            },
        )
        connection.execute(
            tables["experiment_plan_bindings"].insert(),
            {
                "id": "binding-1",
                "space_id": "space-1",
                "parent_algorithm_id": "parent-1",
                "plan_id": "plan-1",
                "plan_version": 1,
                "teacher_id": "teacher-1",
                "created_at": now,
            },
        )
        connection.execute(
            tables["student_assignments"].insert(),
            {
                "id": "assignment-1",
                "binding_id": "binding-1",
                "space_id": "space-1",
                "parent_algorithm_id": "parent-1",
                "child_algorithm_id": "child-1",
                "workbench_id": "workbench-1",
                "student_id": "student-1",
                "plan_id": "plan-1",
                "plan_version": 1,
                "status": "pending_acceptance",
                "scheduled_start_at": now,
                "scheduled_end_at": now,
                "created_at": now,
                "updated_at": now,
            },
        )
        connection.execute(
            tables["student_assignments"].insert(),
            {
                "id": "assignment-plan-2",
                "binding_id": "binding-1",
                "space_id": "space-1",
                "parent_algorithm_id": "parent-1",
                "child_algorithm_id": "child-1",
                "workbench_id": "workbench-1",
                "student_id": "student-1",
                "plan_id": "plan-2",
                "plan_version": 1,
                "status": "pending_acceptance",
                "scheduled_start_at": now,
                "scheduled_end_at": now,
                "created_at": now,
                "updated_at": now,
            },
        )
        connection.execute(
            tables["monitor_sessions"].insert(),
            {
                "id": "session-1",
                "assignment_id": "assignment-1",
                "plan_id": "plan-1",
                "plan_version": 1,
                "status": "collecting",
                "scheduled_end_at": now,
                "actual_end_at": now,
                "evidence_cutoff_at": now,
                "last_activity_at": now,
                "last_heartbeat_at": now,
                "last_contiguous_sequence": 0,
                "missing_ranges": [],
                "completeness": "complete",
                "active_slot": 1,
                "created_at": now,
                "updated_at": now,
            },
        )
        connection.execute(
            tables["evidence_chunks"].insert(),
            {
                "id": "chunk-1",
                "session_id": "session-1",
                "sequence": 1,
                "content_sha256": "b" * 64,
                "content_encoding": "gzip",
                "media_type": "application/json",
                "compressed_bytes": 1,
                "uncompressed_bytes": 1,
                "first_event_sequence": 1,
                "last_event_sequence": 1,
                "object_key": "classrooms/class-1/sessions/session-1/chunks/00000001.json.gz",
                "created_at": now,
            },
        )

    with engine.connect() as connection:
        connection.execute(text("PRAGMA foreign_keys = ON"))
        with pytest.raises(IntegrityError):
            connection.execute(
                tables["student_assignments"].insert(),
                {
                    "id": "assignment-duplicate",
                    "binding_id": "binding-1",
                    "space_id": "space-1",
                    "parent_algorithm_id": "parent-1",
                    "child_algorithm_id": "child-1",
                    "workbench_id": "workbench-2",
                    "student_id": "student-1",
                    "plan_id": "plan-1",
                    "plan_version": 1,
                    "status": "pending_acceptance",
                    "scheduled_start_at": now,
                    "scheduled_end_at": now,
                    "created_at": now,
                    "updated_at": now,
                },
            )
        connection.rollback()

        with pytest.raises(IntegrityError):
            connection.execute(
                tables["classroom_tickets"].insert(),
                {
                    "id": "ticket-orphan",
                    "assignment_id": "missing-assignment",
                    "ticket_hash": "d" * 64,
                    "expires_at": now,
                    "created_at": now,
                },
            )
        connection.rollback()

        with pytest.raises(IntegrityError):
            connection.execute(
                tables["monitor_sessions"].insert(),
                {
                    "id": "session-duplicate",
                    "assignment_id": "assignment-1",
                    "plan_id": "plan-1",
                    "plan_version": 1,
                    "status": "collecting",
                    "scheduled_end_at": now,
                    "actual_end_at": now,
                    "evidence_cutoff_at": now,
                    "last_activity_at": now,
                    "last_heartbeat_at": now,
                    "last_contiguous_sequence": 0,
                    "missing_ranges": [],
                    "completeness": "complete",
                    "active_slot": 1,
                    "created_at": now,
                    "updated_at": now,
                },
            )
        connection.rollback()

        with pytest.raises(IntegrityError):
            connection.execute(
                tables["evidence_chunks"].insert(),
                {
                    "id": "chunk-duplicate",
                    "session_id": "session-1",
                    "sequence": 1,
                    "content_sha256": "c" * 64,
                    "content_encoding": "gzip",
                    "media_type": "application/json",
                    "compressed_bytes": 1,
                    "uncompressed_bytes": 1,
                    "first_event_sequence": 1,
                    "last_event_sequence": 1,
                    "object_key": "classrooms/class-1/sessions/session-1/chunks/00000001-other.json.gz",
                    "created_at": now,
                },
            )


def test_analysis_job_migration_has_one_source_brief_idempotency_key(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'classroom-analysis.db'}"
    config = migration_config(database_url)

    command.upgrade(config, "head")

    inspector = inspect(create_engine(database_url))
    assert ClassroomBriefAnalysisJob.__tablename__ == "classroom_brief_analysis_jobs"
    assert "classroom_brief_analysis_jobs" in inspector.get_table_names()
    assert any(
        constraint["column_names"] == ["source_brief_id"]
        for constraint in inspector.get_unique_constraints("classroom_brief_analysis_jobs")
    )


def test_plan_suggestion_job_migration_has_owner_scoped_active_request_key(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'classroom-plan-suggestions.db'}"
    config = migration_config(database_url)

    command.upgrade(config, "head")

    inspector = inspect(create_engine(database_url))
    assert ClassroomPlanSuggestionJob.__tablename__ == "classroom_plan_suggestion_jobs"
    assert "classroom_plan_suggestion_jobs" in inspector.get_table_names()
    assert any(
        constraint["column_names"]
        == ["teacher_id", "space_id", "parent_algorithm_id", "request_hash", "active_slot"]
        for constraint in inspector.get_unique_constraints("classroom_plan_suggestion_jobs")
    )


def test_student_brief_migration_has_session_scoped_submission_idempotency_key(
    tmp_path: Path,
):
    database_url = f"sqlite:///{tmp_path / 'classroom-brief-idempotency.db'}"
    config = migration_config(database_url)

    command.upgrade(config, "head")

    inspector = inspect(create_engine(database_url))
    assert StudentBrief.__tablename__ == "student_briefs"
    assert "submission_id" in {
        column["name"] for column in inspector.get_columns("student_briefs")
    }
    assert "submission_hash" in {
        column["name"] for column in inspector.get_columns("student_briefs")
    }
    assert any(
        constraint["column_names"] == ["session_id", "submission_id"]
        for constraint in inspector.get_unique_constraints("student_briefs")
    )
    assert "analysis_manifest" in {
        column["name"] for column in inspector.get_columns("evidence_chunks")
    }
