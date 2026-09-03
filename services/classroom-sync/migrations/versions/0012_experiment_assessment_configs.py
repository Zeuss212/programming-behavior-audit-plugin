"""Persist assessment configs independently by experiment scope.

Revision ID: 0012_experiment_assessments
Revises: 0011_experiment_resources
Create Date: 2026-09-02
"""

from __future__ import annotations

from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision = "0012_experiment_assessments"
down_revision = "0011_experiment_resources"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "experiment_assessment_configs",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("space_id", sa.String(length=128), nullable=False),
        sa.Column("parent_algorithm_id", sa.String(length=128), nullable=False),
        sa.Column("experiment_name", sa.String(length=200), nullable=False),
        sa.Column("teacher_id", sa.String(length=128), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("config_revision", sa.Integer(), nullable=False),
        sa.Column("monitoring_scopes", sa.JSON(), nullable=False),
        sa.Column("evaluation_dimensions", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "space_id",
            "parent_algorithm_id",
            name="uq_experiment_assessment_configs_scope",
        ),
    )

    connection = op.get_bind()
    legacy_rows = connection.execute(
        sa.text(
            "SELECT d.space_id, d.parent_algorithm_id, d.title, d.teacher_id, "
            "c.schema_version, c.config_revision, c.monitoring_scopes, "
            "c.evaluation_dimensions, c.created_at, c.updated_at "
            "FROM assessment_configs c JOIN plan_drafts d ON d.id = c.draft_id "
            "ORDER BY c.updated_at DESC"
        )
    ).mappings()
    seen: set[tuple[str, str]] = set()
    table = sa.table(
        "experiment_assessment_configs",
        sa.column("id", sa.String()),
        sa.column("space_id", sa.String()),
        sa.column("parent_algorithm_id", sa.String()),
        sa.column("experiment_name", sa.String()),
        sa.column("teacher_id", sa.String()),
        sa.column("schema_version", sa.Integer()),
        sa.column("config_revision", sa.Integer()),
        sa.column("monitoring_scopes", sa.JSON()),
        sa.column("evaluation_dimensions", sa.JSON()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    for row in legacy_rows:
        scope = (row["space_id"], row["parent_algorithm_id"])
        if scope in seen:
            continue
        seen.add(scope)
        connection.execute(
            table.insert().values(
                id=str(uuid4()),
                space_id=row["space_id"],
                parent_algorithm_id=row["parent_algorithm_id"],
                experiment_name=row["title"],
                teacher_id=row["teacher_id"],
                schema_version=row["schema_version"],
                config_revision=row["config_revision"],
                monitoring_scopes=row["monitoring_scopes"],
                evaluation_dimensions=row["evaluation_dimensions"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
        )


def downgrade() -> None:
    op.drop_table("experiment_assessment_configs")
