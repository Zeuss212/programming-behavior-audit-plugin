"""Scope student assignments to a classroom plan.

Revision ID: 0003_versioned_assignments
Revises: 0002_brief_analysis_jobs
Create Date: 2026-08-16

Downgrading after multiple retained assignments exist for the same student child
across different plan IDs requires restoring a pre-migration database backup:
the legacy unique key is stricter and cannot preserve those rows together.
"""

from __future__ import annotations

from alembic import op

revision = "0003_versioned_assignments"
down_revision = "0002_brief_analysis_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("student_assignments") as batch_op:
        batch_op.drop_constraint("uq_student_assignments_student_child", type_="unique")
        batch_op.create_unique_constraint(
            "uq_student_assignments_plan_student_child",
            ["plan_id", "space_id", "parent_algorithm_id", "student_id", "child_algorithm_id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("student_assignments") as batch_op:
        batch_op.drop_constraint("uq_student_assignments_plan_student_child", type_="unique")
        batch_op.create_unique_constraint(
            "uq_student_assignments_student_child",
            ["space_id", "parent_algorithm_id", "student_id", "child_algorithm_id"],
        )
