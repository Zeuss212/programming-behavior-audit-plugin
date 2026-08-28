"""Persist plan series and retain the source draft for each published version.

Revision ID: 0009_plan_series
Revises: 0008_plan_authoring_sessions
Create Date: 2026-08-29
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009_plan_series"
down_revision = "0008_plan_authoring_sessions"
branch_labels = None
depends_on = None

_DRAFT_NAMING_CONVENTION = {"uq": "uq_%(table_name)s_%(column_0_name)s"}


def _draft_profile_unique_constraint_name(bind: sa.Connection) -> str:
    for constraint in sa.inspect(bind).get_unique_constraints("plan_drafts"):
        if constraint["column_names"] == ["profile_id"] and constraint["name"]:
            return constraint["name"]
    return "uq_plan_drafts_profile_id"


def upgrade() -> None:
    bind = op.get_bind()
    draft_profile_constraint = _draft_profile_unique_constraint_name(bind)

    op.create_table(
        "plan_series",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("profile_id", sa.String(length=64), nullable=False, unique=True),
        sa.Column("space_id", sa.String(length=128), nullable=False),
        sa.Column("parent_algorithm_id", sa.String(length=128), nullable=False),
        sa.Column("latest_version", sa.Integer(), nullable=False),
    )

    with op.batch_alter_table(
        "plan_drafts", naming_convention=_DRAFT_NAMING_CONVENTION
    ) as batch_op:
        batch_op.add_column(sa.Column("plan_id", sa.String(length=64), nullable=True))
        batch_op.drop_constraint(draft_profile_constraint, type_="unique")

    with op.batch_alter_table("plan_versions") as batch_op:
        batch_op.add_column(
            sa.Column("source_draft_id", sa.String(length=64), nullable=True)
        )

    metadata = sa.MetaData()
    drafts = sa.Table("plan_drafts", metadata, autoload_with=bind)
    versions = sa.Table("plan_versions", metadata, autoload_with=bind)
    plan_series = sa.Table("plan_series", metadata, autoload_with=bind)

    bind.execute(drafts.update().values(plan_id=drafts.c.id))
    bind.execute(versions.update().values(source_draft_id=versions.c.plan_id))

    draft_rows = bind.execute(
        sa.select(
            drafts.c.plan_id,
            drafts.c.profile_id,
            drafts.c.space_id,
            drafts.c.parent_algorithm_id,
        )
    ).mappings()
    for draft in draft_rows:
        latest_version = bind.execute(
            sa.select(sa.func.coalesce(sa.func.max(versions.c.version), 0)).where(
                versions.c.plan_id == draft["plan_id"]
            )
        ).scalar_one()
        bind.execute(
            plan_series.insert().values(
                id=draft["plan_id"],
                profile_id=draft["profile_id"],
                space_id=draft["space_id"],
                parent_algorithm_id=draft["parent_algorithm_id"],
                latest_version=latest_version,
            )
        )

    with op.batch_alter_table("plan_drafts") as batch_op:
        batch_op.alter_column(
            "plan_id",
            existing_type=sa.String(length=64),
            nullable=False,
        )

    with op.batch_alter_table("plan_versions") as batch_op:
        batch_op.alter_column(
            "source_draft_id",
            existing_type=sa.String(length=64),
            nullable=False,
        )
        batch_op.create_unique_constraint(
            "uq_plan_versions_source_draft_revision",
            ["source_draft_id", "source_draft_revision"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    metadata = sa.MetaData()
    drafts = sa.Table("plan_drafts", metadata, autoload_with=bind)
    duplicate_profile = bind.execute(
        sa.select(drafts.c.profile_id)
        .group_by(drafts.c.profile_id)
        .having(sa.func.count() > 1)
        .limit(1)
    ).first()
    if duplicate_profile is not None:
        raise RuntimeError("plan_series_downgrade_requires_backup")

    with op.batch_alter_table("plan_versions") as batch_op:
        batch_op.drop_constraint(
            "uq_plan_versions_source_draft_revision",
            type_="unique",
        )
        batch_op.drop_column("source_draft_id")

    with op.batch_alter_table("plan_drafts") as batch_op:
        batch_op.create_unique_constraint("uq_plan_drafts_profile_id", ["profile_id"])
        batch_op.drop_column("plan_id")

    op.drop_table("plan_series")
