"""Add a stable idempotency key for plugin brief submissions.

Revision ID: 0006_brief_submission_id
Revises: 0005_plan_suggestion_jobs
Create Date: 2026-08-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006_brief_submission_id"
down_revision = "0005_plan_suggestion_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("student_briefs") as batch_op:
        batch_op.add_column(sa.Column("submission_id", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("submission_hash", sa.String(length=64), nullable=True))
        batch_op.create_unique_constraint(
            "uq_student_briefs_submission_id",
            ["session_id", "submission_id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("student_briefs") as batch_op:
        batch_op.drop_constraint("uq_student_briefs_submission_id", type_="unique")
        batch_op.drop_column("submission_hash")
        batch_op.drop_column("submission_id")
