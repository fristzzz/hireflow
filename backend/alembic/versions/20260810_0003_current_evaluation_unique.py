"""保证每份简历只有一个当前筛选结果。"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260810_0003"
down_revision = "20260810_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    indexes = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes("evaluations")}
    if "uq_evaluation_current_resume" not in indexes:
        op.create_index(
            "uq_evaluation_current_resume",
            "evaluations",
            ["resume_id"],
            unique=True,
            postgresql_where=sa.text("is_current AND deleted_at IS NULL"),
            sqlite_where=sa.text("is_current = 1 AND deleted_at IS NULL"),
        )


def downgrade() -> None:
    indexes = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes("evaluations")}
    if "uq_evaluation_current_resume" in indexes:
        op.drop_index("uq_evaluation_current_resume", table_name="evaluations")
