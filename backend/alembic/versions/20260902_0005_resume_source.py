"""为简历记录来源（上传 / 邮箱捕获）。"""

from alembic import op
import sqlalchemy as sa

revision = "20260902_0005"
down_revision = "20260829_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "source" not in {column["name"] for column in sa.inspect(op.get_bind()).get_columns("resumes")}:
        op.add_column("resumes", sa.Column("source", sa.String(length=30), nullable=False, server_default="upload"))


def downgrade() -> None:
    op.drop_column("resumes", "source")
