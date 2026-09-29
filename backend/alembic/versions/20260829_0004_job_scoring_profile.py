"""为岗位和筛选任务保存界面量尺（硬性 / 加分项）。"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260829_0004"
down_revision = "20260810_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "scoring_profile" not in {column["name"] for column in sa.inspect(op.get_bind()).get_columns("jobs")}:
        op.add_column("jobs", sa.Column("scoring_profile", sa.JSON(), nullable=True))
    if "scoring_profile_snapshot" not in {column["name"] for column in sa.inspect(op.get_bind()).get_columns("screening_runs")}:
        op.add_column("screening_runs", sa.Column("scoring_profile_snapshot", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("screening_runs", "scoring_profile_snapshot")
    op.drop_column("jobs", "scoring_profile")
