"""Create durable jobs for asynchronous classroom brief analysis.

Revision ID: 0002_brief_analysis_jobs
Revises: 0001_classroom_core
Create Date: 2026-08-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_brief_analysis_jobs"
down_revision = "0001_classroom_core"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "classroom_brief_analysis_jobs",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("source_brief_id", sa.String(length=64), nullable=False),
        sa.Column("run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("failure_code", sa.String(length=128), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["source_brief_id"], ["student_briefs.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("source_brief_id", name="uq_classroom_brief_analysis_jobs_source_brief"),
    )
    op.create_index(
        "ix_classroom_brief_analysis_jobs_status_run_at",
        "classroom_brief_analysis_jobs",
        ["status", "run_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_classroom_brief_analysis_jobs_status_run_at",
        table_name="classroom_brief_analysis_jobs",
    )
    op.drop_table("classroom_brief_analysis_jobs")
