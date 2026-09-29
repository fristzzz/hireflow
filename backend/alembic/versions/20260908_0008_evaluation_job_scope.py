"""将当前评分的唯一范围改为简历与岗位，并补齐历史岗位评分。"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260908_0008"
down_revision = "20260903_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    has_job_id = "job_id" in {column["name"] for column in sa.inspect(op.get_bind()).get_columns("evaluations")}
    if not has_job_id:
        op.add_column("evaluations", sa.Column("job_id", sa.String(36), nullable=True))
    op.execute(sa.text("UPDATE evaluations SET job_id = (SELECT job_id FROM screening_runs WHERE screening_runs.id = evaluations.run_id)"))
    op.drop_index("uq_evaluation_current_resume", table_name="evaluations")
    # 保留原有当前项；其他岗位优先恢复最近的成功结果。
    op.execute(sa.text("""
        UPDATE evaluations SET is_current = CASE WHEN id IN (
            SELECT id FROM (
                SELECT e.id, ROW_NUMBER() OVER (
                    PARTITION BY e.resume_id, e.job_id
                    ORDER BY e.is_current DESC,
                        CASE WHEN e.status = 'completed' THEN 0 ELSE 1 END,
                        e.created_at DESC, e.id DESC
                ) AS position
                FROM evaluations e JOIN screening_runs r ON r.id = e.run_id
                WHERE e.deleted_at IS NULL AND r.deleted_at IS NULL
            ) ranked WHERE position = 1
        ) THEN true ELSE false END
        WHERE deleted_at IS NULL
    """))
    if not has_job_id:
        with op.batch_alter_table("evaluations") as batch:
            batch.alter_column("job_id", existing_type=sa.String(36), nullable=False)
            batch.create_foreign_key("fk_evaluation_job", "jobs", ["job_id"], ["id"], ondelete="CASCADE")
            batch.create_index("ix_evaluations_job_id", ["job_id"])
    if "uq_evaluation_current_resume_job" not in {index["name"] for index in sa.inspect(op.get_bind()).get_indexes("evaluations")}:
        op.create_index("uq_evaluation_current_resume_job", "evaluations", ["resume_id", "job_id"], unique=True,
                    postgresql_where=sa.text("is_current AND deleted_at IS NULL"),
                    sqlite_where=sa.text("is_current = 1 AND deleted_at IS NULL"))


def downgrade() -> None:
    op.drop_index("uq_evaluation_current_resume_job", table_name="evaluations")
    op.execute(sa.text("""
        UPDATE evaluations SET is_current = false WHERE deleted_at IS NULL AND id IN (
            SELECT id FROM (
                SELECT id, ROW_NUMBER() OVER (PARTITION BY resume_id ORDER BY updated_at DESC, id DESC) AS position
                FROM evaluations WHERE is_current = true AND deleted_at IS NULL
            ) ranked WHERE position > 1
        )
    """))
    with op.batch_alter_table("evaluations") as batch:
        batch.drop_index("ix_evaluations_job_id")
        batch.drop_constraint("fk_evaluation_job", type_="foreignkey")
        batch.drop_column("job_id")
    op.create_index("uq_evaluation_current_resume", "evaluations", ["resume_id"], unique=True,
                    postgresql_where=sa.text("is_current AND deleted_at IS NULL"),
                    sqlite_where=sa.text("is_current = 1 AND deleted_at IS NULL"))
