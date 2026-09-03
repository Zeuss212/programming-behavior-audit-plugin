"""Persist experiment creation data required for assessment publication.

Revision ID: 0013_experiment_publication_ctx
Revises: 0012_experiment_assessments
Create Date: 2026-09-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0013_experiment_publication_ctx"
down_revision = "0012_experiment_assessments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "experiment_publication_contexts",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("space_id", sa.String(length=128), nullable=False),
        sa.Column("parent_algorithm_id", sa.String(length=128), nullable=False),
        sa.Column("experiment_name", sa.String(length=200), nullable=False),
        sa.Column("statement", sa.String(length=10000), nullable=False),
        sa.Column("scheduled_start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scheduled_end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ai_policy", sa.String(length=32), nullable=False),
        sa.Column("teacher_id", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "space_id",
            "parent_algorithm_id",
            name="uq_experiment_publication_contexts_scope",
        ),
    )


def downgrade() -> None:
    op.drop_table("experiment_publication_contexts")
