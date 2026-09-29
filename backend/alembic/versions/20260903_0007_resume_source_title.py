"""为邮箱收取的简历记录来源职位名（用于简历池小字注明）。"""

from alembic import op
import sqlalchemy as sa

revision = "20260903_0007"
down_revision = "20260903_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "source_title" not in {column["name"] for column in sa.inspect(op.get_bind()).get_columns("resumes")}:
        op.add_column("resumes", sa.Column("source_title", sa.String(length=200), nullable=True))


def downgrade() -> None:
    op.drop_column("resumes", "source_title")
