"""简历允许不属岗位：job_id 可空表示留在简历池。"""

from alembic import op
import sqlalchemy as sa

revision = "20260903_0006"
down_revision = "20260902_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("resumes") as batch:
        batch.alter_column("job_id", existing_type=sa.String(length=36), nullable=True)


def downgrade() -> None:
    # 回滚前需确保没有池内（job_id 为空）简历
    with op.batch_alter_table("resumes") as batch:
        batch.alter_column("job_id", existing_type=sa.String(length=36), nullable=False)
