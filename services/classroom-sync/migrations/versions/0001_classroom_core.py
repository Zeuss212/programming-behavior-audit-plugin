"""Create persistent state for the classroom monitoring workflow.

Revision ID: 0001_classroom_core
Revises:
Create Date: 2026-08-12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001_classroom_core"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "plan_drafts",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("space_id", sa.String(length=128), nullable=False),
        sa.Column("parent_algorithm_id", sa.String(length=128), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("profile", sa.JSON(), nullable=False),
        sa.Column("scheduled_start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scheduled_end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ai_policy", sa.String(length=32), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("teacher_id", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "plan_versions",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("plan_id", sa.String(length=64), nullable=False),
        sa.Column("profile_id", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("space_id", sa.String(length=128), nullable=False),
        sa.Column("parent_algorithm_id", sa.String(length=128), nullable=False),
        sa.Column("profile", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("scheduled_start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scheduled_end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ai_policy", sa.String(length=32), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("teacher_id", sa.String(length=128), nullable=False),
        sa.UniqueConstraint("profile_id", "version", name="uq_plan_versions_profile_version"),
        sa.UniqueConstraint("plan_id", "version", name="uq_plan_versions_plan_version"),
    )
    op.create_table(
        "experiment_plan_bindings",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("space_id", sa.String(length=128), nullable=False),
        sa.Column("parent_algorithm_id", sa.String(length=128), nullable=False),
        sa.Column("plan_id", sa.String(length=64), nullable=False),
        sa.Column("plan_version", sa.Integer(), nullable=False),
        sa.Column("teacher_id", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "space_id",
            "parent_algorithm_id",
            name="uq_experiment_plan_bindings_parent_experiment",
        ),
    )
    op.create_table(
        "student_assignments",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("binding_id", sa.String(length=64), nullable=False),
        sa.Column("space_id", sa.String(length=128), nullable=False),
        sa.Column("parent_algorithm_id", sa.String(length=128), nullable=False),
        sa.Column("child_algorithm_id", sa.String(length=128), nullable=False),
        sa.Column("workbench_id", sa.String(length=128), nullable=False),
        sa.Column("student_id", sa.String(length=128), nullable=False),
        sa.Column("plan_id", sa.String(length=64), nullable=False),
        sa.Column("plan_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("scheduled_start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scheduled_end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["binding_id"], ["experiment_plan_bindings.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint(
            "space_id",
            "parent_algorithm_id",
            "student_id",
            "child_algorithm_id",
            name="uq_student_assignments_student_child",
        ),
    )
    op.create_table(
        "classroom_tickets",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("assignment_id", sa.String(length=64), nullable=False),
        sa.Column("ticket_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("plugin_instance_hash", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["assignment_id"], ["student_assignments.id"], ondelete="CASCADE"),
    )
    op.create_table(
        "monitor_sessions",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("assignment_id", sa.String(length=64), nullable=False),
        sa.Column("plan_id", sa.String(length=64), nullable=False),
        sa.Column("plan_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("scheduled_end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actual_end_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("evidence_cutoff_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_contiguous_sequence", sa.Integer(), nullable=False),
        sa.Column("missing_ranges", sa.JSON(), nullable=False),
        sa.Column("completeness", sa.String(length=32), nullable=False),
        sa.Column("submission_reason", sa.String(length=32), nullable=True),
        sa.Column("active_slot", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["assignment_id"], ["student_assignments.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("assignment_id", "active_slot", name="uq_monitor_sessions_active_slot"),
    )
    op.create_table(
        "evidence_chunks",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("session_id", sa.String(length=64), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("content_encoding", sa.String(length=32), nullable=False),
        sa.Column("media_type", sa.String(length=128), nullable=False),
        sa.Column("compressed_bytes", sa.Integer(), nullable=False),
        sa.Column("uncompressed_bytes", sa.Integer(), nullable=False),
        sa.Column("first_event_sequence", sa.Integer(), nullable=False),
        sa.Column("last_event_sequence", sa.Integer(), nullable=False),
        sa.Column("object_key", sa.String(length=512), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["monitor_sessions.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("session_id", "sequence", name="uq_evidence_chunks_sequence"),
    )
    op.create_table(
        "student_briefs",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("session_id", sa.String(length=64), nullable=False),
        sa.Column("assignment_id", sa.String(length=64), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("data_completeness", sa.String(length=32), nullable=False),
        sa.Column("submission_reason", sa.String(length=32), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["monitor_sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["assignment_id"], ["student_assignments.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("session_id", "revision", name="uq_student_briefs_revision"),
    )
    op.create_table(
        "teacher_reviews",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("session_id", sa.String(length=64), nullable=False),
        sa.Column("teacher_id", sa.String(length=128), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["monitor_sessions.id"], ondelete="CASCADE"),
    )
    op.create_table(
        "classroom_deadline_jobs",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("session_id", sa.String(length=64), nullable=False, unique=True),
        sa.Column("run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["monitor_sessions.id"], ondelete="CASCADE"),
    )
    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("actor_id", sa.String(length=128), nullable=True),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("entity_type", sa.String(length=64), nullable=False),
        sa.Column("entity_id", sa.String(length=64), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("audit_events")
    op.drop_table("classroom_deadline_jobs")
    op.drop_table("teacher_reviews")
    op.drop_table("student_briefs")
    op.drop_table("evidence_chunks")
    op.drop_table("monitor_sessions")
    op.drop_table("classroom_tickets")
    op.drop_table("student_assignments")
    op.drop_table("experiment_plan_bindings")
    op.drop_table("plan_versions")
    op.drop_table("plan_drafts")
