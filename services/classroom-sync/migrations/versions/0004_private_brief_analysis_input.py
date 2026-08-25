"""Persist private evidence-constrained brief analysis input.

Revision ID: 0004_private_analysis_input
Revises: 0003_versioned_assignments
Create Date: 2026-08-18
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_private_analysis_input"
down_revision = "0003_versioned_assignments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("classroom_brief_analysis_jobs") as batch_op:
        batch_op.add_column(
            sa.Column(
                "analysis_input",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("classroom_brief_analysis_jobs") as batch_op:
        batch_op.drop_column("analysis_input")
