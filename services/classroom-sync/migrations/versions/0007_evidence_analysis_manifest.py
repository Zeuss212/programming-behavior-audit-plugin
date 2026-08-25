"""Add trusted event classifications for private analysis inputs.

Revision ID: 0007_evidence_analysis_manifest
Revises: 0006_brief_submission_id
Create Date: 2026-08-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007_evidence_analysis_manifest"
down_revision = "0006_brief_submission_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("evidence_chunks") as batch_op:
        batch_op.add_column(sa.Column("analysis_manifest", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("evidence_chunks") as batch_op:
        batch_op.drop_column("analysis_manifest")
