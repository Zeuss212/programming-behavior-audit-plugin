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
    "plan_series",
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
                "source_draft_id": "plan-1",
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


def test_identity_defaults_preserve_legacy_direct_model_writes():
    """Existing direct fixtures retain the draft and source identities they implied."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    now = datetime(2026, 8, 29, 8, 0, tzinfo=UTC)
    tables = Base.metadata.tables

    with engine.begin() as connection:
        connection.execute(
            tables["plan_drafts"].insert(),
            {
                "id": "legacy-draft",
                "profile_id": "legacy-profile",
                "space_id": "space-1",
                "parent_algorithm_id": "parent-1",
                "title": "Legacy direct draft",
                "profile": {"schema_version": 2},
                "scheduled_start_at": now,
                "scheduled_end_at": now,
                "ai_policy": "prohibited",
                "revision": 0,
                "teacher_id": "teacher-1",
                "created_at": now,
                "updated_at": now,
            },
        )
        connection.execute(
            tables["plan_versions"].insert(),
            {
                "id": "legacy-version",
                "plan_id": "legacy-draft",
                "profile_id": "legacy-profile",
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

    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT plan_id FROM plan_drafts WHERE id = 'legacy-draft'")
        ).scalar_one() == "legacy-draft"
        assert connection.execute(
            text("SELECT source_draft_id FROM plan_versions WHERE id = 'legacy-version'")
        ).scalar_one() == "legacy-draft"


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


def test_plan_authoring_session_migration_preserves_legacy_rows_and_constraints(
    tmp_path: Path,
):
    """Sessions add nullable one-to-one links without weakening request idempotency."""
    database_url = f"sqlite:///{tmp_path / 'classroom-plan-authoring-sessions.db'}"
    config = migration_config(database_url)
    now = datetime(2026, 8, 27, 8, 0, tzinfo=UTC)

    command.upgrade(config, "0007_evidence_analysis_manifest")
    engine = create_engine(database_url)
    metadata = Base.metadata.__class__()
    metadata.reflect(engine)
    with engine.begin() as connection:
        connection.execute(
            metadata.tables["plan_drafts"].insert(),
            {
                "id": "legacy-draft",
                "profile_id": "legacy-profile",
                "space_id": "space-1",
                "parent_algorithm_id": "parent-1",
                "title": "Legacy draft",
                "profile": {"schema_version": 2},
                "scheduled_start_at": now,
                "scheduled_end_at": now,
                "ai_policy": "prohibited",
                "revision": 0,
                "teacher_id": "teacher-1",
                "created_at": now,
                "updated_at": now,
            },
        )
        connection.execute(
            metadata.tables["classroom_plan_suggestion_jobs"].insert(),
            {
                "id": "legacy-job",
                "teacher_id": "teacher-1",
                "space_id": "space-1",
                "parent_algorithm_id": "parent-1",
                "request_hash": "a" * 64,
                "suggestion_input": {},
                "run_at": now,
                "status": "pending",
                "active_slot": 1,
                "attempts": 0,
                "created_at": now,
                "updated_at": now,
            },
        )

    command.upgrade(config, "0008_plan_authoring_sessions")
    authoring_sessions = inspect(engine).get_table_names()
    assert "plan_authoring_sessions" in authoring_sessions

    reflected = metadata.__class__()
    reflected.reflect(engine)
    sessions = reflected.tables["plan_authoring_sessions"]
    drafts = reflected.tables["plan_drafts"]
    suggestion_jobs = reflected.tables["classroom_plan_suggestion_jobs"]
    inspector = inspect(engine)
    assert {
        "id",
        "teacher_id",
        "space_id",
        "parent_algorithm_id",
        "status",
        "active_slot",
        "suggestion_job_id",
        "published_plan_id",
        "created_at",
        "updated_at",
        "closed_at",
    } <= {column["name"] for column in inspector.get_columns("plan_authoring_sessions")}
    assert {"authoring_session_id"} <= {
        column["name"] for column in inspector.get_columns("plan_drafts")
    }
    assert {"authoring_session_id"} <= {
        column["name"]
        for column in inspector.get_columns("classroom_plan_suggestion_jobs")
    }
    assert not inspector.get_foreign_keys("plan_authoring_sessions")
    assert {index["column_names"][0] for index in inspector.get_indexes("plan_authoring_sessions")} >= {
        "suggestion_job_id",
        "published_plan_id",
    }

    with engine.connect() as connection:
        connection.execute(text("PRAGMA foreign_keys = ON"))
        assert connection.execute(
            text("SELECT authoring_session_id FROM plan_drafts WHERE id = 'legacy-draft'")
        ).scalar_one() is None
        assert connection.execute(
            text(
                "SELECT authoring_session_id FROM classroom_plan_suggestion_jobs "
                "WHERE id = 'legacy-job'"
            )
        ).scalar_one() is None

        connection.execute(
            sessions.insert(),
            {
                "id": "authoring-session-1",
                "teacher_id": "teacher-1",
                "space_id": "space-1",
                "parent_algorithm_id": "parent-1",
                "status": "open",
                "active_slot": 1,
                "created_at": now,
                "updated_at": now,
            },
        )
        connection.commit()
        with pytest.raises(IntegrityError):
            connection.execute(
                sessions.insert(),
                {
                    "id": "authoring-session-duplicate",
                    "teacher_id": "teacher-1",
                    "space_id": "space-1",
                    "parent_algorithm_id": "parent-1",
                    "status": "open",
                    "active_slot": 1,
                    "created_at": now,
                    "updated_at": now,
                },
            )
        connection.rollback()

        for session_id in ("closed-session-1", "closed-session-2"):
            connection.execute(
                sessions.insert(),
                {
                    "id": session_id,
                    "teacher_id": "teacher-1",
                    "space_id": "space-1",
                    "parent_algorithm_id": "parent-1",
                    "status": "closed",
                    "created_at": now,
                    "updated_at": now,
                    "closed_at": now,
                },
            )
        connection.commit()

        connection.execute(
            suggestion_jobs.update()
            .where(suggestion_jobs.c.id == "legacy-job")
            .values(authoring_session_id="authoring-session-1")
        )
        connection.commit()
        with pytest.raises(IntegrityError):
            connection.execute(
                suggestion_jobs.insert(),
                {
                    "id": "job-duplicate-session",
                    "teacher_id": "teacher-1",
                    "space_id": "space-1",
                    "parent_algorithm_id": "parent-1",
                    "request_hash": "b" * 64,
                    "suggestion_input": {},
                    "run_at": now,
                    "status": "pending",
                    "active_slot": 2,
                    "attempts": 0,
                    "authoring_session_id": "authoring-session-1",
                    "created_at": now,
                    "updated_at": now,
                },
            )
        connection.rollback()

        with pytest.raises(IntegrityError):
            connection.execute(
                suggestion_jobs.insert(),
                {
                    "id": "job-duplicate-request",
                    "teacher_id": "teacher-1",
                    "space_id": "space-1",
                    "parent_algorithm_id": "parent-1",
                    "request_hash": "a" * 64,
                    "suggestion_input": {},
                    "run_at": now,
                    "status": "pending",
                    "active_slot": 1,
                    "attempts": 0,
                    "created_at": now,
                    "updated_at": now,
                },
            )
        connection.rollback()

        connection.execute(
            drafts.update()
            .where(drafts.c.id == "legacy-draft")
            .values(authoring_session_id="authoring-session-1")
        )
        connection.commit()
        with pytest.raises(IntegrityError):
            connection.execute(
                drafts.insert(),
                {
                    "id": "draft-duplicate-session",
                    "profile_id": "other-profile",
                    "space_id": "space-1",
                    "parent_algorithm_id": "parent-1",
                    "title": "Duplicate session draft",
                    "profile": {"schema_version": 2},
                    "scheduled_start_at": now,
                    "scheduled_end_at": now,
                    "ai_policy": "prohibited",
                    "revision": 0,
                    "teacher_id": "teacher-1",
                    "authoring_session_id": "authoring-session-1",
                    "created_at": now,
                    "updated_at": now,
                },
            )
        connection.rollback()

        with pytest.raises(IntegrityError):
            connection.execute(sessions.delete().where(sessions.c.id == "authoring-session-1"))
        connection.rollback()

    command.downgrade(config, "0007_evidence_analysis_manifest")
    inspector = inspect(engine)
    assert "plan_authoring_sessions" not in inspector.get_table_names()
    assert "authoring_session_id" not in {
        column["name"] for column in inspector.get_columns("plan_drafts")
    }
    assert "authoring_session_id" not in {
        column["name"]
        for column in inspector.get_columns("classroom_plan_suggestion_jobs")
    }
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT id FROM plan_drafts WHERE id = 'legacy-draft'")
            ).scalar_one()
            == "legacy-draft"
        )
        assert connection.execute(
            text("SELECT id FROM classroom_plan_suggestion_jobs WHERE id = 'legacy-job'")
        ).scalar_one() == "legacy-job"


def test_plan_series_migration_backfills_legacy_drafts_and_source_versions(
    tmp_path: Path,
):
    """Legacy drafts become reusable series and versions retain their source draft."""
    database_url = f"sqlite:///{tmp_path / 'classroom-plan-series.db'}"
    config = migration_config(database_url)
    now = datetime(2026, 8, 29, 8, 0, tzinfo=UTC)

    command.upgrade(config, "0008_plan_authoring_sessions")
    engine = create_engine(database_url)
    legacy_metadata = Base.metadata.__class__()
    legacy_metadata.reflect(engine)
    legacy_tables = legacy_metadata.tables
    with engine.begin() as connection:
        connection.execute(
            legacy_tables["plan_drafts"].insert(),
            {
                "id": "legacy-draft",
                "profile_id": "legacy-profile",
                "space_id": "space-1",
                "parent_algorithm_id": "parent-1",
                "title": "Legacy draft",
                "profile": {"schema_version": 2},
                "scheduled_start_at": now,
                "scheduled_end_at": now,
                "ai_policy": "prohibited",
                "revision": 2,
                "teacher_id": "teacher-1",
                "created_at": now,
                "updated_at": now,
            },
        )
        for version in (1, 2):
            connection.execute(
                legacy_tables["plan_versions"].insert(),
                {
                    "id": f"legacy-version-{version}",
                    "plan_id": "legacy-draft",
                    "profile_id": "legacy-profile",
                    "version": version,
                    "source_draft_revision": version,
                    "space_id": "space-1",
                    "parent_algorithm_id": "parent-1",
                    "profile": {"schema_version": 2},
                    "content_hash": str(version) * 64,
                    "scheduled_start_at": now,
                    "scheduled_end_at": now,
                    "ai_policy": "prohibited",
                    "published_at": now,
                    "teacher_id": "teacher-1",
                },
            )
        connection.execute(
            legacy_tables["plan_versions"].insert(),
            {
                "id": "orphan-version-1",
                "plan_id": "orphan-plan",
                "profile_id": "orphan-profile",
                "version": 1,
                "source_draft_revision": 0,
                "space_id": "space-2",
                "parent_algorithm_id": "parent-2",
                "profile": {"schema_version": 2},
                "content_hash": "o" * 64,
                "scheduled_start_at": now,
                "scheduled_end_at": now,
                "ai_policy": "prohibited",
                "published_at": now,
                "teacher_id": "teacher-2",
            },
        )

    command.upgrade(config, "head")

    with engine.begin() as connection:
        assert connection.execute(
            text("SELECT plan_id FROM plan_drafts WHERE id = 'legacy-draft'")
        ).scalar_one() == "legacy-draft"
        assert connection.execute(
            text("SELECT source_draft_id FROM plan_versions WHERE id = 'legacy-version-2'")
        ).scalar_one() == "legacy-draft"
        assert connection.execute(
            text("SELECT latest_version FROM plan_series WHERE id = 'legacy-draft'")
        ).scalar_one() == 2
        assert connection.execute(
            text("SELECT latest_version FROM plan_series WHERE id = 'orphan-plan'")
        ).scalar_one() == 1

        reflected = Base.metadata.__class__()
        reflected.reflect(connection)
        drafts = reflected.tables["plan_drafts"]
        versions = reflected.tables["plan_versions"]

        connection.execute(
            drafts.insert(),
            {
                "id": "second-draft",
                "plan_id": "legacy-draft",
                "profile_id": "legacy-profile",
                "space_id": "space-1",
                "parent_algorithm_id": "parent-1",
                "title": "Second draft",
                "profile": {"schema_version": 2},
                "scheduled_start_at": now,
                "scheduled_end_at": now,
                "ai_policy": "prohibited",
                "revision": 0,
                "teacher_id": "teacher-1",
                "created_at": now,
                "updated_at": now,
            },
        )

        with pytest.raises(IntegrityError):
            connection.execute(
                versions.insert(),
                {
                    "id": "duplicate-source-revision",
                    "plan_id": "legacy-draft",
                    "profile_id": "duplicate-profile",
                    "version": 3,
                    "source_draft_id": "legacy-draft",
                    "source_draft_revision": 1,
                    "space_id": "space-1",
                    "parent_algorithm_id": "parent-1",
                    "profile": {"schema_version": 2},
                    "content_hash": "d" * 64,
                    "scheduled_start_at": now,
                    "scheduled_end_at": now,
                    "ai_policy": "prohibited",
                    "published_at": now,
                    "teacher_id": "teacher-1",
                },
            )


def test_plan_series_downgrade_requires_backup_for_duplicate_draft_profiles(
    tmp_path: Path,
):
    """Downgrading must not silently discard a profile reused by one plan series."""
    database_url = f"sqlite:///{tmp_path / 'classroom-plan-series-downgrade.db'}"
    config = migration_config(database_url)
    now = datetime(2026, 8, 29, 8, 0, tzinfo=UTC)

    command.upgrade(config, "head")
    engine = create_engine(database_url)
    reflected = Base.metadata.__class__()
    reflected.reflect(engine)
    drafts = reflected.tables["plan_drafts"]
    with engine.begin() as connection:
        for draft_id in ("draft-1", "draft-2"):
            connection.execute(
                drafts.insert(),
                {
                    "id": draft_id,
                    "plan_id": "plan-1",
                    "profile_id": "shared-profile",
                    "space_id": "space-1",
                    "parent_algorithm_id": "parent-1",
                    "title": "Draft sharing profile",
                    "profile": {"schema_version": 2},
                    "scheduled_start_at": now,
                    "scheduled_end_at": now,
                    "ai_policy": "prohibited",
                    "revision": 0,
                    "teacher_id": "teacher-1",
                    "created_at": now,
                    "updated_at": now,
                },
            )

    with pytest.raises(RuntimeError, match="plan_series_downgrade_requires_backup"):
        command.downgrade(config, "0008_plan_authoring_sessions")

    assert "plan_series" in inspect(engine).get_table_names()


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
