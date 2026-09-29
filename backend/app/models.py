"""定义岗位、简历、筛选运行和评估记录。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid4())


class SoftDeleteMixin:
    """为可恢复资源提供统一的回收站时间。"""

    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    purge_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)


class Job(SoftDeleteMixin, Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200))
    jd_text: Mapped[str] = mapped_column(Text)
    prompt: Mapped[str] = mapped_column(Text)
    scoring_profile: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    resumes: Mapped[list[Resume]] = relationship(back_populates="job", cascade="all, delete-orphan")
    runs: Mapped[list[ScreeningRun]] = relationship(back_populates="job", cascade="all, delete-orphan")


class Resume(SoftDeleteMixin, Base):
    __tablename__ = "resumes"
    __table_args__ = (UniqueConstraint("job_id", "sha256", name="uq_resume_job_sha256"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), nullable=True, index=True)
    original_name: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    mime_type: Mapped[str] = mapped_column(String(120))
    storage_path: Mapped[str] = mapped_column(Text)
    preview_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    parse_status: Mapped[str] = mapped_column(String(30), default="uploaded")
    parsed_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    parsed_text_source: Mapped[str] = mapped_column(String(30), default="extracted")
    parsed_text_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    parse_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(30), default="upload")
    source_title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    identity_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    identity_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    identity_phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    identity_status: Mapped[str] = mapped_column(String(20), default="none")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    job: Mapped[Job | None] = relationship(back_populates="resumes")
    evaluations: Mapped[list[Evaluation]] = relationship(back_populates="resume", cascade="all, delete-orphan")


class ScreeningRun(SoftDeleteMixin, Base):
    __tablename__ = "screening_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    jd_snapshot: Mapped[str] = mapped_column(Text)
    prompt_snapshot: Mapped[str] = mapped_column(Text)
    scoring_profile_snapshot: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    text_model: Mapped[str] = mapped_column(String(160))
    vision_model: Mapped[str] = mapped_column(String(160))
    schema_version: Mapped[str] = mapped_column(String(20), default="1")
    status: Mapped[str] = mapped_column(String(30), default="queued")
    total_count: Mapped[int] = mapped_column(Integer, default=0)
    completed_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    job: Mapped[Job] = relationship(back_populates="runs")
    evaluations: Mapped[list[Evaluation]] = relationship(back_populates="run", cascade="all, delete-orphan")


class Evaluation(SoftDeleteMixin, Base):
    __tablename__ = "evaluations"
    __table_args__ = (
        UniqueConstraint("run_id", "resume_id", name="uq_evaluation_run_resume"),
        Index(
            "uq_evaluation_current_resume_job",
            "resume_id",
            "job_id",
            unique=True,
            postgresql_where=text("is_current AND deleted_at IS NULL"),
            sqlite_where=text("is_current = 1 AND deleted_at IS NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", name="fk_evaluation_job", ondelete="CASCADE"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("screening_runs.id", ondelete="CASCADE"), index=True)
    resume_id: Mapped[str] = mapped_column(ForeignKey("resumes.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(30), default="queued")
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    is_current: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    replaces_evaluation_id: Mapped[str | None] = mapped_column(
        ForeignKey("evaluations.id", ondelete="SET NULL"), nullable=True, index=True
    )
    review_status: Mapped[str] = mapped_column(String(30), default="pending")
    review_note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    run: Mapped[ScreeningRun] = relationship(back_populates="evaluations")
    resume: Mapped[Resume] = relationship(back_populates="evaluations")
