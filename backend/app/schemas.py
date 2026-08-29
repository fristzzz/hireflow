"""定义 HTTP 接口与模型评估的权威数据结构。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class JobCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    jd_text: str = Field(min_length=1, max_length=100_000)
    prompt: str = Field(min_length=1, max_length=50_000)


class JobUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    jd_text: str | None = Field(default=None, min_length=1, max_length=100_000)
    prompt: str | None = Field(default=None, min_length=1, max_length=50_000)


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    jd_text: str
    prompt: str
    resume_count: int = 0
    run_count: int = 0
    created_at: datetime
    updated_at: datetime


class ResumeUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=255)
    parsed_text: str | None = Field(default=None, min_length=1, max_length=500_000)


class ResumeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    job_id: str
    original_name: str
    display_name: str
    mime_type: str
    size_bytes: int
    page_count: int | None
    parse_status: str
    parse_error: str | None
    parsed_text_source: Literal["extracted", "manual"]
    parsed_text_updated_at: datetime | None
    created_at: datetime
    updated_at: datetime


class UploadRejection(BaseModel):
    filename: str
    reason: str
    recoverable_resume_id: str | None = None


class UploadResult(BaseModel):
    uploaded: list[ResumeOut]
    rejected: list[UploadRejection]


class Evidence(BaseModel):
    text: str
    location: str


class BackgroundHighlight(BaseModel):
    kind: Literal["company", "school"]
    name: str = Field(min_length=1, max_length=200)
    bonus_points: int = Field(default=0, ge=0, le=3)
    evidence: list[Evidence] = Field(default_factory=list)


class Criterion(BaseModel):
    criterion_id: str = ""
    criterion: str
    weight: int = Field(default=0, ge=0, le=100)
    score: int | None = Field(default=None, ge=0, le=100)
    weighted_score: float = Field(default=0, ge=0, le=100)
    status: Literal["met", "partial", "not_met", "unknown"]
    evidence: list[Evidence] = Field(default_factory=list)


class CandidateProfile(BaseModel):
    name: str = "未识别"
    email: str | None = None
    phone: str | None = None
    current_role: str | None = None
    years_experience: float | None = None


class DraftCriterion(BaseModel):
    criterion_id: str = Field(pattern=r"^D\d{2}$")
    score: int = Field(ge=0, le=100)
    status: Literal["met", "partial", "not_met", "unknown"]
    evidence: list[Evidence] = Field(default_factory=list)


class EvaluationDraft(BaseModel):
    candidate: CandidateProfile = Field(default_factory=CandidateProfile)
    confidence: float = Field(ge=0, le=1)
    summary: str
    criteria: list[DraftCriterion]
    background_highlights: list[BackgroundHighlight] = Field(default_factory=list, max_length=4)
    strengths: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    interview_questions: list[str] = Field(default_factory=list)


class EvaluationCard(BaseModel):
    scoring_version: str = "1"
    candidate: CandidateProfile = Field(default_factory=CandidateProfile)
    overall_score: int = Field(ge=0, le=100)
    match_level: Literal["high", "medium", "low"]
    confidence: float = Field(ge=0, le=1)
    summary: str
    criteria: list[Criterion] = Field(default_factory=list)
    background_highlights: list[BackgroundHighlight] = Field(default_factory=list, max_length=4)
    background_bonus: int = Field(default=0, ge=0, le=5)
    strengths: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    interview_questions: list[str] = Field(default_factory=list)


class RunCreate(BaseModel):
    resume_ids: list[str] | None = None


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    job_id: str
    status: str
    total_count: int
    completed_count: int
    failed_count: int
    text_model: str
    vision_model: str
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class RunPage(BaseModel):
    items: list[RunOut]
    page: int
    page_size: int
    total: int
    total_pages: int


class EvaluationOut(BaseModel):
    id: str
    run_id: str
    resume_id: str
    resume_name: str
    status: str
    result: EvaluationCard | None
    error: str | None
    attempts: int
    review_status: str
    review_note: str
    screened_at: datetime
    updated_at: datetime


class EvaluationIds(BaseModel):
    evaluation_ids: list[str] = Field(min_length=1, max_length=200)


class BatchReviewUpdate(EvaluationIds):
    status: Literal["pending", "advance", "reject", "hold"]


class BatchActionResult(BaseModel):
    updated_count: int


class EvaluationPage(BaseModel):
    items: list[EvaluationOut]
    page: int
    page_size: int
    total: int
    total_pages: int


class ReviewUpdate(BaseModel):
    status: Literal["pending", "advance", "reject", "hold"]
    note: str = Field(default="", max_length=10_000)


class TrashItem(BaseModel):
    item_type: Literal["job", "resume", "run", "evaluation"]
    id: str
    name: str
    job_id: str
    job_name: str
    deleted_at: datetime
    purge_at: datetime


class TrashPage(BaseModel):
    items: list[TrashItem]
    page: int
    page_size: int
    total: int
    total_pages: int


class TrashReference(BaseModel):
    item_type: Literal["job", "resume", "run", "evaluation"]
    id: str


class TrashBatchAction(BaseModel):
    items: list[TrashReference] = Field(min_length=1, max_length=200)
