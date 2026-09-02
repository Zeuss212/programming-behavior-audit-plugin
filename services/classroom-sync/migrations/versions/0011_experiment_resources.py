"""Persist teacher-owned experiment resources.

Revision ID: 0011_experiment_resources
Revises: 0010_assessment_configs
Create Date: 2026-09-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011_experiment_resources"
down_revision = "0010_assessment_configs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "experiment_resources",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("space_id", sa.String(length=128), nullable=False),
        sa.Column("parent_algorithm_id", sa.String(length=128), nullable=False),
        sa.Column("teacher_id", sa.String(length=128), nullable=False),
        sa.Column("resource_kind", sa.String(length=32), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=128), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("download_only", sa.Boolean(), nullable=False),
        sa.Column("object_key", sa.String(length=512), nullable=False, unique=True),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "space_id",
            "parent_algorithm_id",
            "resource_kind",
            "filename",
            "content_sha256",
            name="uq_experiment_resources_scope_kind_name_hash",
        ),
    )
    op.create_index(
        "ix_experiment_resources_scope_kind_created",
        "experiment_resources",
        ["space_id", "parent_algorithm_id", "resource_kind", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_experiment_resources_scope_kind_created",
        table_name="experiment_resources",
    )
    op.drop_table("experiment_resources")
