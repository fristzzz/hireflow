"""增加工作台生命周期、文本修正和当前评分字段。"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260810_0002"
down_revision = "20260807_0001"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def _indexes(table: str) -> set[str]:
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table)}


def upgrade() -> None:
    for table in ("jobs", "resumes", "screening_runs", "evaluations"):
        columns = _columns(table)
        if "deleted_at" not in columns:
            op.add_column(table, sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
        if "purge_at" not in columns:
            op.add_column(table, sa.Column("purge_at", sa.DateTime(timezone=True), nullable=True))
        indexes = _indexes(table)
        if f"ix_{table}_deleted_at" not in indexes:
            op.create_index(f"ix_{table}_deleted_at", table, ["deleted_at"])
        if f"ix_{table}_purge_at" not in indexes:
            op.create_index(f"ix_{table}_purge_at", table, ["purge_at"])

    resume_columns = _columns("resumes")
    if "display_name" not in resume_columns:
        op.add_column("resumes", sa.Column("display_name", sa.String(length=255), nullable=True))
    if "parsed_text_source" not in resume_columns:
        op.add_column(
            "resumes",
            sa.Column("parsed_text_source", sa.String(length=30), nullable=False, server_default="extracted"),
        )
    if "parsed_text_updated_at" not in resume_columns:
        op.add_column("resumes", sa.Column("parsed_text_updated_at", sa.DateTime(timezone=True), nullable=True))
    if "updated_at" not in resume_columns:
        op.add_column("resumes", sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE resumes SET display_name = original_name WHERE display_name IS NULL")
    op.execute("UPDATE resumes SET updated_at = created_at WHERE updated_at IS NULL")

    evaluation_columns = _columns("evaluations")
    if "is_current" not in evaluation_columns:
        op.add_column(
            "evaluations",
            sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.false()),
        )
    if "replaces_evaluation_id" not in evaluation_columns:
        op.add_column("evaluations", sa.Column("replaces_evaluation_id", sa.String(length=36), nullable=True))
        if op.get_bind().dialect.name == "postgresql":
            op.create_foreign_key(
                "fk_evaluations_replaces_evaluation_id",
                "evaluations",
                "evaluations",
                ["replaces_evaluation_id"],
                ["id"],
                ondelete="SET NULL",
            )
    evaluation_indexes = _indexes("evaluations")
    if "ix_evaluations_is_current" not in evaluation_indexes:
        op.create_index("ix_evaluations_is_current", "evaluations", ["is_current"])
    if "ix_evaluations_replaces_evaluation_id" not in evaluation_indexes:
        op.create_index("ix_evaluations_replaces_evaluation_id", "evaluations", ["replaces_evaluation_id"])
    op.execute("UPDATE evaluations SET is_current = false")
    op.execute(
        """
        UPDATE evaluations
        SET is_current = true
        WHERE id IN (
            SELECT id FROM (
                SELECT id, ROW_NUMBER() OVER (
                    PARTITION BY resume_id
                    ORDER BY CASE WHEN status = 'completed' THEN 0 ELSE 1 END, updated_at DESC
                ) AS row_number
                FROM evaluations
            ) ranked
            WHERE row_number = 1
        )
        """
    )
    evaluation_indexes = _indexes("evaluations")
    if "uq_evaluation_current_resume" not in evaluation_indexes:
        op.create_index(
            "uq_evaluation_current_resume",
            "evaluations",
            ["resume_id"],
            unique=True,
            postgresql_where=sa.text("is_current AND deleted_at IS NULL"),
            sqlite_where=sa.text("is_current = 1 AND deleted_at IS NULL"),
        )


def downgrade() -> None:
    # 回收站数据无法安全还原为旧版物理删除语义，因此仅移除新增索引和列。
    for index in ("uq_evaluation_current_resume", "ix_evaluations_replaces_evaluation_id", "ix_evaluations_is_current"):
        if index in _indexes("evaluations"):
            op.drop_index(index, table_name="evaluations")
    for column in ("replaces_evaluation_id", "is_current"):
        if column in _columns("evaluations"):
            op.drop_column("evaluations", column)
    for column in ("updated_at", "parsed_text_updated_at", "parsed_text_source", "display_name"):
        if column in _columns("resumes"):
            op.drop_column("resumes", column)
    for table in ("evaluations", "screening_runs", "resumes", "jobs"):
        for index in (f"ix_{table}_purge_at", f"ix_{table}_deleted_at"):
            if index in _indexes(table):
                op.drop_index(index, table_name=table)
        for column in ("purge_at", "deleted_at"):
            if column in _columns(table):
                op.drop_column(table, column)
