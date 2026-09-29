"""resumes 增加结构化身份四列，存量池简历回填为待提取。"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260915_0009"
down_revision = "20260908_0008"
branch_labels = None
depends_on = None

_IDENTITY_COLUMNS = {
    "identity_name": sa.Column("identity_name", sa.String(200), nullable=True),
    "identity_email": sa.Column("identity_email", sa.String(320), nullable=True),
    "identity_phone": sa.Column("identity_phone", sa.String(50), nullable=True),
    "identity_status": sa.Column("identity_status", sa.String(20), nullable=False, server_default="none"),
}


def upgrade() -> None:
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("resumes")}
    with op.batch_alter_table("resumes") as batch:
        for name, column in _IDENTITY_COLUMNS.items():
            if name not in existing:
                batch.add_column(column)
    # 存量池简历统一交给后台身份提取循环；直传岗位的简历不提取
    op.execute(sa.text("UPDATE resumes SET identity_status = 'pending' WHERE job_id IS NULL AND deleted_at IS NULL"))


def downgrade() -> None:
    with op.batch_alter_table("resumes") as batch:
        for name in _IDENTITY_COLUMNS:
            batch.drop_column(name)
