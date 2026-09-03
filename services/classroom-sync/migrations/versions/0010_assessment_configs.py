"""Persist assessment configs and immutable plan-version snapshots.

Revision ID: 0010_assessment_configs
Revises: 0009_plan_series
Create Date: 2026-09-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010_assessment_configs"
down_revision = "0009_plan_series"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("plan_versions") as batch_op:
        batch_op.add_column(
            sa.Column("content_schema_version", sa.Integer(), nullable=True)
        )
        batch_op.add_column(sa.Column("assessment_config", sa.JSON(), nullable=True))

    op.execute("UPDATE plan_versions SET content_schema_version = 1")

    with op.batch_alter_table("plan_versions") as batch_op:
        batch_op.alter_column(
            "content_schema_version",
            existing_type=sa.Integer(),
            nullable=False,
            server_default="1",
        )

    op.create_table(
        "assessment_configs",
        sa.Column(
            "draft_id",
            sa.String(length=64),
            sa.ForeignKey("plan_drafts.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("config_revision", sa.Integer(), nullable=False),
        sa.Column("monitoring_scopes", sa.JSON(), nullable=False),
        sa.Column("evaluation_dimensions", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("assessment_configs")
    with op.batch_alter_table("plan_versions") as batch_op:
        batch_op.drop_column("assessment_config")
        batch_op.drop_column("content_schema_version")
