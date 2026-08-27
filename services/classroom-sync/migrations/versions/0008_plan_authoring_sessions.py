"""Add persistent plan authoring sessions and compatibility links.

Revision ID: 0008_plan_authoring_sessions
Revises: 0007_evidence_analysis_manifest
Create Date: 2026-08-28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008_plan_authoring_sessions"
down_revision = "0007_evidence_analysis_manifest"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "plan_authoring_sessions",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("teacher_id", sa.String(length=128), nullable=False),
        sa.Column("space_id", sa.String(length=200), nullable=False),
        sa.Column("parent_algorithm_id", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("active_slot", sa.Integer(), nullable=True),
        sa.Column("suggestion_job_id", sa.String(length=64), nullable=True),
        sa.Column("published_plan_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "teacher_id",
            "space_id",
            "parent_algorithm_id",
            "active_slot",
            name="uq_plan_authoring_sessions_open",
        ),
    )
    op.create_index(
        "ix_plan_authoring_sessions_suggestion_job_id",
        "plan_authoring_sessions",
        ["suggestion_job_id"],
    )
    op.create_index(
        "ix_plan_authoring_sessions_published_plan_id",
        "plan_authoring_sessions",
        ["published_plan_id"],
    )

    with op.batch_alter_table("plan_drafts") as batch_op:
        batch_op.add_column(sa.Column("authoring_session_id", sa.String(length=64), nullable=True))
        batch_op.create_foreign_key(
            "fk_plan_drafts_authoring_session",
            "plan_authoring_sessions",
            ["authoring_session_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch_op.create_unique_constraint(
            "uq_plan_drafts_authoring_session",
            ["authoring_session_id"],
        )

    with op.batch_alter_table("classroom_plan_suggestion_jobs") as batch_op:
        batch_op.add_column(sa.Column("authoring_session_id", sa.String(length=64), nullable=True))
        batch_op.create_foreign_key(
            "fk_classroom_plan_suggestion_jobs_authoring_session",
            "plan_authoring_sessions",
            ["authoring_session_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch_op.create_unique_constraint(
            "uq_classroom_plan_suggestion_jobs_authoring_session",
            ["authoring_session_id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("classroom_plan_suggestion_jobs") as batch_op:
        batch_op.drop_constraint(
            "uq_classroom_plan_suggestion_jobs_authoring_session",
            type_="unique",
        )
        batch_op.drop_constraint(
            "fk_classroom_plan_suggestion_jobs_authoring_session",
            type_="foreignkey",
        )
        batch_op.drop_column("authoring_session_id")

    with op.batch_alter_table("plan_drafts") as batch_op:
        batch_op.drop_constraint("uq_plan_drafts_authoring_session", type_="unique")
        batch_op.drop_constraint("fk_plan_drafts_authoring_session", type_="foreignkey")
        batch_op.drop_column("authoring_session_id")

    op.drop_index(
        "ix_plan_authoring_sessions_published_plan_id",
        table_name="plan_authoring_sessions",
    )
    op.drop_index(
        "ix_plan_authoring_sessions_suggestion_job_id",
        table_name="plan_authoring_sessions",
    )
    op.drop_table("plan_authoring_sessions")
