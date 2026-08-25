"""Add durable, privacy-minimized plan suggestion jobs.

Revision ID: 0005_plan_suggestion_jobs
Revises: 0004_private_analysis_input
Create Date: 2026-08-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005_plan_suggestion_jobs"
down_revision = "0004_private_analysis_input"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "classroom_plan_suggestion_jobs",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("teacher_id", sa.String(length=128), nullable=False),
        sa.Column("space_id", sa.String(length=200), nullable=False),
        sa.Column("parent_algorithm_id", sa.String(length=200), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("suggestion_input", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("active_slot", sa.Integer(), nullable=True),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("failure_code", sa.String(length=128), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "teacher_id",
            "space_id",
            "parent_algorithm_id",
            "request_hash",
            "active_slot",
            name="uq_classroom_plan_suggestion_jobs_active_request",
        ),
    )
    op.create_index(
        "ix_classroom_plan_suggestion_jobs_status_run_at",
        "classroom_plan_suggestion_jobs",
        ["status", "run_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_classroom_plan_suggestion_jobs_status_run_at",
        table_name="classroom_plan_suggestion_jobs",
    )
    op.drop_table("classroom_plan_suggestion_jobs")
