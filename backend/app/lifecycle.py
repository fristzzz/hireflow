"""集中处理回收站标记、恢复与物理清理。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import shutil
from typing import Literal

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings
from .models import Evaluation, Job, Resume, ScreeningRun

TrashType = Literal["job", "resume", "run", "evaluation"]
TrashModel = type[Job] | type[Resume] | type[ScreeningRun] | type[Evaluation]

TRASH_MODELS: dict[TrashType, TrashModel] = {
    "job": Job,
    "resume": Resume,
    "run": ScreeningRun,
    "evaluation": Evaluation,
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def move_to_trash(item: Job | Resume | ScreeningRun | Evaluation) -> None:
    """将资源放入保留三十天的回收站。"""

    if item.deleted_at is not None:
        return
    deleted_at = utc_now()
    item.deleted_at = deleted_at
    item.purge_at = deleted_at + timedelta(days=30)


def restore_from_trash(item: Job | Resume | ScreeningRun | Evaluation) -> None:
    item.deleted_at = None
    item.purge_at = None


def delete_resume_files(resume: Resume) -> None:
    Path(resume.storage_path).unlink(missing_ok=True)
    if resume.preview_path:
        Path(resume.preview_path).unlink(missing_ok=True)


def trash_job(job: Job) -> None:
    """软删除岗位，并级联软删除其下简历、任务与评估。"""

    for resume in job.resumes:
        for evaluation in resume.evaluations:
            move_to_trash(evaluation)
        move_to_trash(resume)
    for run in job.runs:
        for evaluation in run.evaluations:
            move_to_trash(evaluation)
        move_to_trash(run)
    move_to_trash(job)


def trash_resume(resume: Resume) -> None:
    """软删除简历档案，并级联软删除其评估结果。"""

    for evaluation in resume.evaluations:
        move_to_trash(evaluation)
    move_to_trash(resume)


def trash_run(run: ScreeningRun) -> None:
    """软删除筛选任务，并级联软删除本批评估。"""

    for evaluation in run.evaluations:
        move_to_trash(evaluation)
    move_to_trash(run)


def trash_evaluation(evaluation: Evaluation) -> None:
    move_to_trash(evaluation)


def _repair_current_flags(resume: Resume) -> None:
    """保证每份活跃简历最多一条 is_current。"""

    active = [item for item in resume.evaluations if item.deleted_at is None]
    currents = [item for item in active if item.is_current]
    if len(currents) > 1:
        currents.sort(key=lambda item: item.updated_at, reverse=True)
        for extra in currents[1:]:
            extra.is_current = False
    elif not currents:
        completed = [item for item in active if item.status == "completed"]
        if completed:
            completed.sort(key=lambda item: item.updated_at, reverse=True)
            completed[0].is_current = True


def restore_job(job: Job) -> None:
    """恢复岗位及其仍在回收站中的下级资源。"""

    restore_from_trash(job)
    for resume in job.resumes:
        if resume.deleted_at is not None:
            restore_from_trash(resume)
    for run in job.runs:
        if run.deleted_at is not None:
            restore_from_trash(run)
        for evaluation in run.evaluations:
            if evaluation.deleted_at is not None:
                restore_from_trash(evaluation)
    for resume in job.resumes:
        _repair_current_flags(resume)


def restore_resume(session: Session, resume: Resume) -> None:
    if resume.job.deleted_at is not None:
        raise HTTPException(status_code=409, detail="请先恢复所属岗位")
    restore_from_trash(resume)
    for evaluation in resume.evaluations:
        if evaluation.deleted_at is not None and evaluation.run.deleted_at is None:
            restore_from_trash(evaluation)
    _repair_current_flags(resume)


def restore_run(run: ScreeningRun) -> None:
    if run.job.deleted_at is not None:
        raise HTTPException(status_code=409, detail="请先恢复所属岗位")
    restore_from_trash(run)
    for evaluation in run.evaluations:
        if evaluation.deleted_at is not None and evaluation.resume.deleted_at is None:
            restore_from_trash(evaluation)
            _repair_current_flags(evaluation.resume)


def restore_evaluation(session: Session, evaluation: Evaluation) -> None:
    if evaluation.resume.deleted_at or evaluation.run.deleted_at or evaluation.run.job.deleted_at:
        raise HTTPException(status_code=409, detail="请先恢复所属简历和筛选任务")
    current = session.scalar(
        select(Evaluation).where(
            Evaluation.resume_id == evaluation.resume_id,
            Evaluation.id != evaluation.id,
            Evaluation.is_current.is_(True),
            Evaluation.deleted_at.is_(None),
        )
    )
    if current:
        raise HTTPException(status_code=409, detail="该简历已有更新的当前评分，旧结果不能恢复")
    restore_from_trash(evaluation)
    evaluation.is_current = True


def is_top_level_trash_item(item_type: TrashType, item: Job | Resume | ScreeningRun | Evaluation) -> bool:
    """回收站只展示直接删除的入口项，级联删除的子项随父级恢复。"""

    if item.deleted_at is None:
        return False
    if item_type == "job":
        return True
    if item_type == "resume":
        resume = item
        assert isinstance(resume, Resume)
        return resume.job.deleted_at is None
    if item_type == "run":
        run = item
        assert isinstance(run, ScreeningRun)
        return run.job.deleted_at is None
    evaluation = item
    assert isinstance(evaluation, Evaluation)
    return (
        evaluation.resume.deleted_at is None
        and evaluation.run.deleted_at is None
        and evaluation.run.job.deleted_at is None
    )


def purge_item(
    session: Session,
    settings: Settings,
    item_type: TrashType,
    item: Job | Resume | ScreeningRun | Evaluation,
) -> None:
    """物理删除资源；数据库级联负责删除下级记录。"""

    if item_type == "job":
        job = item
        assert isinstance(job, Job)
        shutil.rmtree(settings.uploads_dir / job.id, ignore_errors=True)
        shutil.rmtree(settings.derived_dir / job.id, ignore_errors=True)
    elif item_type == "resume":
        resume = item
        assert isinstance(resume, Resume)
        delete_resume_files(resume)
    session.delete(item)


def purge_expired(session: Session, settings: Settings) -> int:
    """清理所有已超过保留期的顶层回收项。"""

    current = utc_now()
    purged = 0
    # 先清父对象，避免其子对象同时进入清理列表后重复处理。
    for item_type in ("job", "resume", "run", "evaluation"):
        model = TRASH_MODELS[item_type]
        items = list(
            session.scalars(
                select(model).where(model.deleted_at.is_not(None), model.purge_at <= current)
            ).all()
        )
        for item in items:
            if session.get(model, item.id) is None:
                continue
            purge_item(session, settings, item_type, item)
            purged += 1
        session.flush()
    session.commit()
    return purged
