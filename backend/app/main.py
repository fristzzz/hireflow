"""创建 FastAPI 应用并注册工作台 HTTP 接口。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import asynccontextmanager
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
import asyncio
from typing import Any, Literal
from uuid import uuid4

from fastapi import BackgroundTasks, Depends, FastAPI, File, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import Integer, String, cast, func, or_, select
from sqlalchemy.orm import Session

from .config import Settings
from .database import Base, create_database
from .documents import DocumentError, DocumentProcessor
from .names import display_name_from_filename
from .errors import public_failure_message
from .evaluator import Evaluator, OpenAIEvaluator, compile_scoring_dimensions
from .lifecycle import (
    TRASH_MODELS,
    TrashType,
    is_top_level_trash_item,
    purge_expired,
    purge_item,
    restore_evaluation,
    restore_job,
    restore_resume,
    restore_run,
    trash_evaluation,
    trash_job,
    trash_resume,
    trash_run,
)
from .models import Evaluation, Job, Resume, ScreeningRun
from .runner import ScreeningRunner
from .schemas import (
    BatchActionResult,
    BatchReviewUpdate,
    EvaluationIds,
    EvaluationOut,
    EvaluationPage,
    JobCreate,
    JobOut,
    JobUpdate,
    ResumeOut,
    ResumeUpdate,
    ReviewUpdate,
    RunCreate,
    RunOut,
    RunPage,
    TrashBatchAction,
    TrashItem,
    TrashPage,
    UploadRejection,
    UploadResult,
)
from .workbench import (
    active_job,
    active_resume,
    active_run,
    export_response,
    require_active_evaluation,
    serialize_evaluation,
    serialize_job,
    serialize_resume,
    serialize_run,
    serialize_trash_item,
    utc_now,
)


def create_app(
    database_url: str | None = None,
    data_dir: Path | None = None,
    settings: Settings | None = None,
    evaluator: Evaluator | None = None,
) -> FastAPI:
    runtime_settings = settings or Settings()
    if database_url:
        runtime_settings.database_url = database_url
        if data_dir is None and database_url.startswith("sqlite:///"):
            runtime_settings.data_dir = Path(database_url.removeprefix("sqlite:///")).parent / "data"
    if data_dir:
        runtime_settings.data_dir = data_dir
    engine, session_factory = create_database(runtime_settings.database_url)

    document_processor = DocumentProcessor(runtime_settings)
    screening_runner = ScreeningRunner(
        session_factory,
        document_processor,
        evaluator or OpenAIEvaluator(runtime_settings),
        runtime_settings,
    )

    async def parse_uploaded_resume(resume_id: str) -> None:
        """在上传响应后解析文件，并用独立数据库会话写回文本。"""

        with session_factory() as parse_session:
            resume = parse_session.get(Resume, resume_id)
            if not resume or resume.deleted_at or resume.parsed_text:
                return
            resume.parse_status = "parsing"
            resume.parse_error = None
            storage_path = resume.storage_path
            parse_session.commit()
        try:
            parsed_text = await document_processor.extract_text(storage_path)
        except Exception as exc:
            with session_factory() as parse_session:
                resume = parse_session.get(Resume, resume_id)
                if resume:
                    resume.parse_status = "failed"
                    resume.parse_error = public_failure_message(exc, "简历解析失败，请重试或改用人工修正")
                    parse_session.commit()
            return
        with session_factory() as parse_session:
            resume = parse_session.get(Resume, resume_id)
            if resume:
                resume.parsed_text = parsed_text
                resume.parsed_text_source = "extracted"
                resume.parsed_text_updated_at = utc_now()
                resume.parse_status = "ready"
                resume.parse_error = None
                parse_session.commit()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        runtime_settings.ensure_directories()
        Base.metadata.create_all(engine)
        screening_runner.recover_interrupted()
        with session_factory() as recovery_session:
            purge_expired(recovery_session, runtime_settings)
            pending_resume_ids = list(
                recovery_session.scalars(
                    select(Resume.id)
                    .join(Resume.job)
                    .where(
                        Resume.deleted_at.is_(None),
                        Job.deleted_at.is_(None),
                        Resume.parsed_text.is_(None),
                        Resume.parse_status.in_(["uploaded", "parsing"]),
                    )
                ).all()
            )
        recovery_tasks = [asyncio.create_task(parse_uploaded_resume(resume_id)) for resume_id in pending_resume_ids]

        async def cleanup_loop() -> None:
            while True:
                await asyncio.sleep(24 * 60 * 60)
                with session_factory() as cleanup_session:
                    purge_expired(cleanup_session, runtime_settings)

        cleanup_task = asyncio.create_task(cleanup_loop())
        try:
            yield
        finally:
            cleanup_task.cancel()
            await asyncio.gather(cleanup_task, return_exceptions=True)
            for task in recovery_tasks:
                if not task.done():
                    task.cancel()
            if recovery_tasks:
                await asyncio.gather(*recovery_tasks, return_exceptions=True)
            await screening_runner.shutdown()
            engine.dispose()

    app = FastAPI(title="HireFlow", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.settings = runtime_settings
    app.state.session_factory = session_factory
    app.state.screening_runner = screening_runner
    app.state.document_processor = document_processor

    def get_session() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "database": "configured",
            "text_model": runtime_settings.text_model,
            "vision_model": runtime_settings.vision_model,
            "vision_enabled": runtime_settings.vision_enabled,
            "model_configured": bool(runtime_settings.openai_api_key),
        }

    @app.post("/api/jobs", response_model=JobOut, status_code=status.HTTP_201_CREATED)
    def create_job(payload: JobCreate, session: Session = Depends(get_session)) -> JobOut:
        try:
            compile_scoring_dimensions(payload.prompt)
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        job = Job(**payload.model_dump())
        session.add(job)
        session.commit()
        return serialize_job(session, job)

    @app.get("/api/jobs", response_model=list[JobOut])
    def list_jobs(session: Session = Depends(get_session)) -> list[JobOut]:
        jobs = session.scalars(select(Job).where(Job.deleted_at.is_(None)).order_by(Job.updated_at.desc())).all()
        return [serialize_job(session, job) for job in jobs]

    @app.get("/api/jobs/{job_id}", response_model=JobOut)
    def get_job(job_id: str, session: Session = Depends(get_session)) -> JobOut:
        job = active_job(session, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="岗位不存在")
        return serialize_job(session, job)

    @app.patch("/api/jobs/{job_id}", response_model=JobOut)
    def update_job(job_id: str, payload: JobUpdate, session: Session = Depends(get_session)) -> JobOut:
        job = active_job(session, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="岗位不存在")
        changes = payload.model_dump(exclude_none=True)
        if "prompt" in changes:
            try:
                compile_scoring_dimensions(changes["prompt"])
            except RuntimeError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        for key, value in changes.items():
            setattr(job, key, value)
        session.commit()
        return serialize_job(session, job)

    @app.delete("/api/jobs/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_job(job_id: str, session: Session = Depends(get_session)) -> None:
        job = active_job(session, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="岗位不存在")
        for run in job.runs:
            if run.status in {"queued", "running"}:
                screening_runner.cancel(run.id)
        trash_job(job)
        session.commit()

    @app.post("/api/jobs/{job_id}/resumes", response_model=UploadResult, status_code=status.HTTP_201_CREATED)
    async def upload_resumes(
        job_id: str,
        background_tasks: BackgroundTasks,
        files: list[UploadFile] = File(...),
        session: Session = Depends(get_session),
    ) -> UploadResult:
        job = active_job(session, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="岗位不存在")
        existing_count = session.scalar(
            select(func.count()).select_from(Resume).where(Resume.job_id == job_id, Resume.deleted_at.is_(None))
        ) or 0
        uploaded: list[ResumeOut] = []
        rejected: list[UploadRejection] = []

        for index, upload in enumerate(files):
            filename = upload.filename or f"未命名文件-{index + 1}"
            if existing_count + len(uploaded) >= runtime_settings.max_resumes_per_job:
                rejected.append(UploadRejection(filename=filename, reason="岗位简历数量已达到上限"))
                continue
            content = await upload.read(runtime_settings.max_file_mb * 1024 * 1024 + 1)
            resume_id = str(uuid4())
            try:
                ingested = document_processor.ingest(
                    job_id=job_id,
                    resume_id=resume_id,
                    filename=filename,
                    mime_type=upload.content_type or "application/octet-stream",
                    content=content,
                )
                duplicate = session.scalar(
                    select(Resume).where(Resume.job_id == job_id, Resume.sha256 == ingested.sha256)
                )
                if duplicate:
                    Path(ingested.storage_path).unlink(missing_ok=True)
                    if ingested.preview_path:
                        Path(ingested.preview_path).unlink(missing_ok=True)
                    reason = (
                        "相同简历仍在回收站，请先恢复旧档案"
                        if duplicate.deleted_at
                        else "该简历内容重复，未再次上传"
                    )
                    rejected.append(
                        UploadRejection(
                            filename=filename,
                            reason=reason,
                            recoverable_resume_id=duplicate.id if duplicate.deleted_at else None,
                        )
                    )
                    continue
                resume = Resume(
                    id=resume_id,
                    job_id=job_id,
                    display_name=display_name_from_filename(ingested.original_name) or ingested.original_name,
                    parsed_text_source="extracted",
                    parsed_text_updated_at=utc_now() if ingested.parsed_text else None,
                    **ingested.__dict__,
                )
                session.add(resume)
                session.commit()
                uploaded.append(serialize_resume(resume))
                if not resume.parsed_text:
                    background_tasks.add_task(parse_uploaded_resume, resume.id)
            except DocumentError as exc:
                session.rollback()
                rejected.append(UploadRejection(filename=filename, reason=str(exc)))
            except Exception:
                session.rollback()
                rejected.append(UploadRejection(filename=filename, reason="文件处理失败"))
        return UploadResult(uploaded=uploaded, rejected=rejected)

    @app.get("/api/jobs/{job_id}/resumes", response_model=list[ResumeOut])
    def list_resumes(job_id: str, session: Session = Depends(get_session)) -> list[ResumeOut]:
        if not active_job(session, job_id):
            raise HTTPException(status_code=404, detail="岗位不存在")
        items = session.scalars(
            select(Resume).where(Resume.job_id == job_id, Resume.deleted_at.is_(None)).order_by(Resume.created_at.desc())
        ).all()
        return [serialize_resume(item) for item in items]

    @app.patch("/api/resumes/{resume_id}", response_model=ResumeOut)
    def update_resume(
        resume_id: str,
        payload: ResumeUpdate,
        session: Session = Depends(get_session),
    ) -> ResumeOut:
        resume = active_resume(session, resume_id)
        if not resume:
            raise HTTPException(status_code=404, detail="简历不存在")
        changes = payload.model_dump(exclude_none=True)
        if "display_name" in changes:
            resume.display_name = changes["display_name"].strip()
        if "parsed_text" in changes:
            if resume.parse_status == "parsing":
                raise HTTPException(status_code=409, detail="简历正在解析，请稍后再编辑")
            resume.parsed_text = changes["parsed_text"].strip()
            resume.parsed_text_source = "manual"
            resume.parsed_text_updated_at = utc_now()
            resume.parse_status = "ready"
            resume.parse_error = None
        session.commit()
        return serialize_resume(resume)

    @app.post("/api/resumes/{resume_id}/reparse", response_model=ResumeOut, status_code=status.HTTP_202_ACCEPTED)
    async def reparse_resume(
        resume_id: str,
        background_tasks: BackgroundTasks,
        session: Session = Depends(get_session),
    ) -> ResumeOut:
        resume = active_resume(session, resume_id)
        if not resume:
            raise HTTPException(status_code=404, detail="简历不存在")
        if resume.parse_status == "parsing":
            raise HTTPException(status_code=409, detail="简历已经在解析中")
        resume.parsed_text = None
        resume.parsed_text_source = "extracted"
        resume.parsed_text_updated_at = None
        resume.parse_status = "uploaded"
        resume.parse_error = None
        session.commit()
        background_tasks.add_task(parse_uploaded_resume, resume.id)
        return serialize_resume(resume)

    @app.delete("/api/resumes/{resume_id}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_resume(resume_id: str, session: Session = Depends(get_session)) -> None:
        resume = active_resume(session, resume_id)
        if not resume:
            raise HTTPException(status_code=404, detail="简历不存在")
        active_evaluations = [
            item for item in resume.evaluations if item.status in {"queued", "parsing", "evaluating"} and not item.deleted_at
        ]
        if active_evaluations:
            raise HTTPException(status_code=409, detail="简历正在筛选，请先取消所属任务")
        trash_resume(resume)
        session.commit()

    @app.get("/api/resumes/{resume_id}/original")
    def original_resume(resume_id: str, session: Session = Depends(get_session)) -> FileResponse:
        resume = active_resume(session, resume_id)
        if not resume or not Path(resume.storage_path).is_file():
            raise HTTPException(status_code=404, detail="简历原件不存在")
        return FileResponse(resume.storage_path, media_type=resume.mime_type, filename=resume.original_name)

    @app.get("/api/resumes/{resume_id}/preview")
    def preview_resume(resume_id: str, session: Session = Depends(get_session)):
        resume = active_resume(session, resume_id)
        if not resume:
            raise HTTPException(status_code=404, detail="简历不存在")
        if resume.original_name.lower().endswith(".pdf"):
            page_count = resume.page_count or 0
            pages = "".join(
                f'<figure><img src="/api/resumes/{resume.id}/preview/pages/{page_number}" '
                f'alt="简历第 {page_number} 页" loading="lazy"><figcaption>第 {page_number} 页</figcaption></figure>'
                for page_number in range(1, page_count + 1)
            )
            html = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><style>
*{{box-sizing:border-box}}body{{margin:0;padding:22px;background:#e7e5df;color:#52605a;font:12px system-ui,sans-serif}}
main{{max-width:1100px;margin:0 auto}}figure{{margin:0 0 22px}}img{{display:block;width:100%;height:auto;background:white;box-shadow:0 3px 18px rgba(27,38,33,.16)}}
figcaption{{padding:8px 0;text-align:center}}.empty{{padding:60px 20px;text-align:center}}
</style></head><body><main>{pages or '<p class="empty">该 PDF 没有可预览的页面</p>'}</main></body></html>"""
            return HTMLResponse(
                html,
                headers={
                    "Cache-Control": "private, no-store",
                    "Content-Security-Policy": "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'",
                },
            )
        if resume.preview_path and Path(resume.preview_path).is_file():
            return HTMLResponse(
                Path(resume.preview_path).read_text(encoding="utf-8"),
                headers={"Cache-Control": "private, no-store"},
            )
        raise HTTPException(status_code=404, detail="简历预览不存在")

    @app.get("/api/resumes/{resume_id}/preview/pages/{page_number}")
    def preview_pdf_page(
        resume_id: str,
        page_number: int,
        session: Session = Depends(get_session),
    ) -> Response:
        resume = active_resume(session, resume_id)
        if not resume or not resume.original_name.lower().endswith(".pdf"):
            raise HTTPException(status_code=404, detail="PDF 简历不存在")
        try:
            image = document_processor.render_pdf_page(resume.storage_path, page_number)
        except DocumentError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return Response(
            image,
            media_type="image/png",
            headers={"Cache-Control": "private, no-store"},
        )

    @app.get("/api/resumes/{resume_id}/text", response_class=PlainTextResponse)
    def resume_text(
        resume_id: str,
        download: bool = False,
        session: Session = Depends(get_session),
    ) -> PlainTextResponse:
        resume = active_resume(session, resume_id)
        if not resume:
            raise HTTPException(status_code=404, detail="简历不存在")
        if resume.parse_status != "ready" or not resume.parsed_text:
            raise HTTPException(status_code=409, detail="简历文本尚未解析完成")
        headers = {"Cache-Control": "private, no-store"}
        if download:
            headers["Content-Disposition"] = f'attachment; filename="resume-{resume.id}.txt"'
        return PlainTextResponse(resume.parsed_text, headers=headers)

    @app.post("/api/jobs/{job_id}/runs", response_model=RunOut, status_code=status.HTTP_201_CREATED)
    async def create_run(job_id: str, payload: RunCreate, session: Session = Depends(get_session)) -> RunOut:
        job = active_job(session, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="岗位不存在")
        try:
            compile_scoring_dimensions(job.prompt)
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        query = select(Resume).where(Resume.job_id == job_id, Resume.deleted_at.is_(None))
        if payload.resume_ids is not None:
            if not payload.resume_ids:
                raise HTTPException(status_code=400, detail="至少选择一份简历")
            query = query.where(Resume.id.in_(payload.resume_ids))
        resumes = list(session.scalars(query).all())
        if not resumes:
            raise HTTPException(status_code=400, detail="岗位下没有可筛选的简历")
        if payload.resume_ids is not None and len(resumes) != len(set(payload.resume_ids)):
            raise HTTPException(status_code=400, detail="部分简历不属于当前岗位")
        run = ScreeningRun(
            job_id=job_id,
            jd_snapshot=job.jd_text,
            prompt_snapshot=job.prompt,
            text_model=runtime_settings.text_model,
            vision_model=runtime_settings.vision_model,
            schema_version="2",
            total_count=len(resumes),
        )
        session.add(run)
        session.flush()
        evaluations: list[Evaluation] = []
        for resume in resumes:
            previous = session.scalar(
                select(Evaluation)
                .where(
                    Evaluation.resume_id == resume.id,
                    Evaluation.is_current.is_(True),
                    Evaluation.deleted_at.is_(None),
                )
                .order_by(Evaluation.updated_at.desc())
            )
            evaluations.append(
                Evaluation(
                    run_id=run.id,
                    resume_id=resume.id,
                    is_current=previous is None,
                    replaces_evaluation_id=previous.id if previous else None,
                )
            )
        session.add_all(evaluations)
        session.commit()
        screening_runner.start(run.id)
        return serialize_run(run)

    @app.get("/api/jobs/{job_id}/runs", response_model=RunPage)
    def list_runs(
        job_id: str,
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=20, ge=1, le=100),
        run_status: str = Query(default="all", max_length=30),
        session: Session = Depends(get_session),
    ) -> RunPage:
        if not active_job(session, job_id):
            raise HTTPException(status_code=404, detail="岗位不存在")
        query = select(ScreeningRun).where(
            ScreeningRun.job_id == job_id,
            ScreeningRun.deleted_at.is_(None),
        )
        if run_status != "all":
            query = query.where(ScreeningRun.status == run_status)
        total = session.scalar(select(func.count()).select_from(query.order_by(None).subquery())) or 0
        items = list(
            session.scalars(
                query.order_by(ScreeningRun.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
            ).all()
        )
        return RunPage(
            items=[serialize_run(item) for item in items],
            page=page,
            page_size=page_size,
            total=total,
            total_pages=(total + page_size - 1) // page_size,
        )

    @app.get("/api/runs/{run_id}", response_model=RunOut)
    def get_run(run_id: str, session: Session = Depends(get_session)) -> RunOut:
        run = active_run(session, run_id)
        if not run:
            raise HTTPException(status_code=404, detail="筛选运行不存在")
        return serialize_run(run, refresh_counts=True)

    @app.get("/api/runs/{run_id}/evaluations", response_model=list[EvaluationOut])
    def list_evaluations(run_id: str, session: Session = Depends(get_session)) -> list[EvaluationOut]:
        if not active_run(session, run_id):
            raise HTTPException(status_code=404, detail="筛选运行不存在")
        items = session.scalars(
            select(Evaluation)
            .where(
                Evaluation.run_id == run_id,
                Evaluation.deleted_at.is_(None),
            )
            .order_by(Evaluation.updated_at.desc())
        ).all()
        return [serialize_evaluation(item) for item in items]

    @app.get("/api/jobs/{job_id}/evaluations", response_model=EvaluationPage)
    def list_job_evaluations(
        job_id: str,
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=20, ge=1, le=100),
        search: str = Query(default="", max_length=200),
        review_status: Literal["all", "pending", "advance", "reject", "hold"] = "all",
        match_level: Literal["all", "high", "medium", "low", "unscored"] = "all",
        run_status: Literal["all", "queued", "running", "completed", "partial", "failed", "canceled"] = "all",
        date_from: date | None = None,
        date_to: date | None = None,
        sort: Literal["score-desc", "score-asc", "name-asc", "screened-desc"] = "screened-desc",
        session: Session = Depends(get_session),
    ) -> EvaluationPage:
        if not active_job(session, job_id):
            raise HTTPException(status_code=404, detail="岗位不存在")
        query = (
            select(Evaluation)
            .join(Evaluation.run)
            .join(Evaluation.resume)
            .where(
                ScreeningRun.job_id == job_id,
                ScreeningRun.deleted_at.is_(None),
                Resume.deleted_at.is_(None),
                Evaluation.deleted_at.is_(None),
                Evaluation.is_current.is_(True),
            )
        )
        if search.strip():
            pattern = f"%{search.strip()}%"
            query = query.where(
                or_(
                    Resume.original_name.ilike(pattern),
                    Resume.display_name.ilike(pattern),
                    cast(Evaluation.result_json, String).ilike(pattern),
                )
            )
        if review_status != "all":
            query = query.where(Evaluation.review_status == review_status)
        if run_status != "all":
            query = query.where(ScreeningRun.status == run_status)
        if date_from:
            query = query.where(ScreeningRun.created_at >= datetime.combine(date_from, time.min, tzinfo=timezone.utc))
        if date_to:
            query = query.where(
                ScreeningRun.created_at < datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=timezone.utc)
            )
        match_expression = Evaluation.result_json["match_level"].as_string()
        if match_level == "unscored":
            query = query.where(Evaluation.result_json.is_(None))
        elif match_level != "all":
            query = query.where(match_expression == match_level)

        total = session.scalar(select(func.count()).select_from(query.order_by(None).subquery())) or 0
        score_expression = cast(Evaluation.result_json["overall_score"].as_string(), Integer)
        name_expression = func.coalesce(
            Evaluation.result_json["candidate"]["name"].as_string(),
            Resume.display_name,
            Resume.original_name,
        )
        if sort == "score-desc":
            query = query.order_by(score_expression.desc().nullslast(), ScreeningRun.created_at.desc())
        elif sort == "score-asc":
            query = query.order_by(score_expression.asc().nullslast(), ScreeningRun.created_at.desc())
        elif sort == "name-asc":
            query = query.order_by(name_expression.asc(), ScreeningRun.created_at.desc())
        else:
            query = query.order_by(ScreeningRun.created_at.desc(), Evaluation.created_at.desc())
        items = list(session.scalars(query.offset((page - 1) * page_size).limit(page_size)).all())
        return EvaluationPage(
            items=[serialize_evaluation(item) for item in items],
            page=page,
            page_size=page_size,
            total=total,
            total_pages=(total + page_size - 1) // page_size,
        )

    @app.patch("/api/evaluations/{evaluation_id}/review", response_model=EvaluationOut)
    def update_review(evaluation_id: str, payload: ReviewUpdate, session: Session = Depends(get_session)) -> EvaluationOut:
        evaluation = require_active_evaluation(session, evaluation_id)
        evaluation.review_status = payload.status
        evaluation.review_note = payload.note
        session.commit()
        return serialize_evaluation(evaluation)

    @app.patch("/api/evaluations/review", response_model=BatchActionResult)
    def batch_update_review(
        payload: BatchReviewUpdate,
        session: Session = Depends(get_session),
    ) -> BatchActionResult:
        requested = list(dict.fromkeys(payload.evaluation_ids))
        items = list(
            session.scalars(
                select(Evaluation)
                .join(Evaluation.run)
                .join(Evaluation.resume)
                .join(ScreeningRun.job)
                .where(
                    Evaluation.id.in_(requested),
                    Evaluation.deleted_at.is_(None),
                    Evaluation.is_current.is_(True),
                    ScreeningRun.deleted_at.is_(None),
                    Resume.deleted_at.is_(None),
                    Job.deleted_at.is_(None),
                )
            ).all()
        )
        if len(items) != len(requested):
            raise HTTPException(status_code=422, detail="部分筛选结果不存在或不可操作")
        for item in items:
            item.review_status = payload.status
        session.commit()
        return BatchActionResult(updated_count=len(items))

    @app.post("/api/evaluations/rescore", response_model=RunOut, status_code=status.HTTP_201_CREATED)
    async def rescore_evaluations(
        payload: EvaluationIds,
        session: Session = Depends(get_session),
    ) -> RunOut:
        requested = list(dict.fromkeys(payload.evaluation_ids))
        items = list(
            session.scalars(
                select(Evaluation)
                .join(Evaluation.run)
                .join(Evaluation.resume)
                .join(ScreeningRun.job)
                .where(
                    Evaluation.id.in_(requested),
                    Evaluation.deleted_at.is_(None),
                    Evaluation.is_current.is_(True),
                    ScreeningRun.deleted_at.is_(None),
                    Resume.deleted_at.is_(None),
                    Job.deleted_at.is_(None),
                )
            ).all()
        )
        if len(items) != len(requested):
            raise HTTPException(status_code=422, detail="部分筛选结果不存在或已被替换")
        job_ids = {item.run.job_id for item in items}
        if len(job_ids) != 1:
            raise HTTPException(status_code=422, detail="只能批量重评同一岗位的候选人")
        return await create_run(job_ids.pop(), RunCreate(resume_ids=[item.resume_id for item in items]), session)

    @app.delete("/api/evaluations/{evaluation_id}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_evaluation(evaluation_id: str, session: Session = Depends(get_session)) -> None:
        evaluation = session.get(Evaluation, evaluation_id)
        if not evaluation or evaluation.deleted_at:
            raise HTTPException(status_code=404, detail="筛选结果不存在")
        if evaluation.run.deleted_at or evaluation.resume.deleted_at or evaluation.run.job.deleted_at:
            raise HTTPException(status_code=404, detail="筛选结果不存在")
        if evaluation.status in {"queued", "parsing", "evaluating"}:
            raise HTTPException(status_code=409, detail="筛选仍在进行，请先取消所属任务")
        trash_evaluation(evaluation)
        session.commit()

    @app.post("/api/evaluations/delete", response_model=BatchActionResult)
    def batch_delete_evaluations(
        payload: EvaluationIds,
        session: Session = Depends(get_session),
    ) -> BatchActionResult:
        requested = list(dict.fromkeys(payload.evaluation_ids))
        items = list(
            session.scalars(
                select(Evaluation).where(
                    Evaluation.id.in_(requested),
                    Evaluation.deleted_at.is_(None),
                )
            ).all()
        )
        if len(items) != len(requested):
            raise HTTPException(status_code=422, detail="部分筛选结果不存在")
        if any(item.status in {"queued", "parsing", "evaluating"} for item in items):
            raise HTTPException(status_code=409, detail="所选结果中仍有任务正在运行")
        for item in items:
            trash_evaluation(item)
        session.commit()
        return BatchActionResult(updated_count=len(items))

    @app.post("/api/runs/{run_id}/cancel", response_model=RunOut)
    async def cancel_run(run_id: str, session: Session = Depends(get_session)) -> RunOut:
        run = active_run(session, run_id)
        if not run:
            raise HTTPException(status_code=404, detail="筛选运行不存在")
        if run.status not in {"queued", "running"}:
            raise HTTPException(status_code=409, detail="任务已结束，无法取消")
        if not screening_runner.cancel(run_id):
            session.refresh(run)
            if run.status not in {"canceled"}:
                raise HTTPException(status_code=409, detail="无法取消该任务")
        session.expire_all()
        run = active_run(session, run_id)
        if not run:
            raise HTTPException(status_code=404, detail="筛选运行不存在")
        return serialize_run(run, refresh_counts=True)

    @app.delete("/api/runs/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_run(run_id: str, session: Session = Depends(get_session)) -> None:
        run = active_run(session, run_id)
        if not run:
            raise HTTPException(status_code=404, detail="筛选任务不存在")
        if run.status in {"queued", "running"}:
            raise HTTPException(status_code=409, detail="任务仍在运行，请先取消后再删除")
        trash_run(run)
        session.commit()

    @app.get("/api/runs/{run_id}/export")
    def export_run(
        run_id: str,
        format: Literal["xlsx", "csv", "json", "md", "docx"] = "xlsx",
        evaluation_id: list[str] | None = Query(default=None),
        session: Session = Depends(get_session),
    ) -> Response:
        run = active_run(session, run_id)
        if not run:
            raise HTTPException(status_code=404, detail="筛选运行不存在")
        items = list(
            session.scalars(
                select(Evaluation)
                .join(Evaluation.resume)
                .where(
                    Evaluation.run_id == run_id,
                    Evaluation.deleted_at.is_(None),
                    Resume.deleted_at.is_(None),
                )
            ).all()
        )
        requested_ids = list(dict.fromkeys(evaluation_id or []))
        if requested_ids:
            items_by_id = {item.id: item for item in items}
            invalid_ids = [item_id for item_id in requested_ids if item_id not in items_by_id]
            if invalid_ids:
                raise HTTPException(status_code=422, detail="所选候选人不属于当前筛选批次")
            items = [items_by_id[item_id] for item_id in requested_ids]
        return export_response(items, format, f"hireflow-{run_id}")

    @app.get("/api/jobs/{job_id}/export")
    def export_job(
        job_id: str,
        format: Literal["xlsx", "csv", "json", "md", "docx"] = "xlsx",
        evaluation_id: list[str] | None = Query(default=None),
        session: Session = Depends(get_session),
    ) -> Response:
        if not active_job(session, job_id):
            raise HTTPException(status_code=404, detail="岗位不存在")
        items = list(
            session.scalars(
                select(Evaluation)
                .join(Evaluation.run)
                .join(Evaluation.resume)
                .where(
                    ScreeningRun.job_id == job_id,
                    ScreeningRun.deleted_at.is_(None),
                    Resume.deleted_at.is_(None),
                    Evaluation.deleted_at.is_(None),
                    Evaluation.is_current.is_(True),
                )
                .order_by(ScreeningRun.created_at.desc(), Evaluation.created_at.desc())
            ).all()
        )
        requested_ids = list(dict.fromkeys(evaluation_id or []))
        if requested_ids:
            items_by_id = {item.id: item for item in items}
            if any(item_id not in items_by_id for item_id in requested_ids):
                raise HTTPException(status_code=422, detail="所选候选人不属于当前岗位")
            items = [items_by_id[item_id] for item_id in requested_ids]
        return export_response(items, format, f"hireflow-job-{job_id}")

    @app.post("/api/runs/{run_id}/retry-failed", response_model=RunOut)
    async def retry_failed(run_id: str, session: Session = Depends(get_session)) -> RunOut:
        run = active_run(session, run_id)
        if not run:
            raise HTTPException(status_code=404, detail="筛选运行不存在")
        failed = [item for item in run.evaluations if item.status == "failed" and not item.deleted_at]
        if not failed:
            raise HTTPException(status_code=400, detail="没有可重试的失败任务")
        for item in failed:
            item.status = "queued"
            item.error = None
        run.status = "queued"
        run.completed_at = None
        session.commit()
        screening_runner.start(run_id)
        return serialize_run(run)

    @app.get("/api/trash", response_model=TrashPage)
    def list_trash(
        item_type: Literal["all", "job", "resume", "run", "evaluation"] = "all",
        job_id: str | None = None,
        search: str = Query(default="", max_length=200),
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=20, ge=1, le=100),
        session: Session = Depends(get_session),
    ) -> TrashPage:
        kinds: list[TrashType] = ["job", "resume", "run", "evaluation"] if item_type == "all" else [item_type]
        items: list[TrashItem] = []
        for kind in kinds:
            model = TRASH_MODELS[kind]
            deleted = session.scalars(select(model).where(model.deleted_at.is_not(None))).all()
            for item in deleted:
                if not is_top_level_trash_item(kind, item):
                    continue
                items.append(serialize_trash_item(kind, item))
        if job_id:
            items = [item for item in items if item.job_id == job_id]
        if search.strip():
            needle = search.strip().casefold()
            items = [item for item in items if needle in item.name.casefold() or needle in item.job_name.casefold()]
        items.sort(key=lambda item: item.deleted_at, reverse=True)
        total = len(items)
        start = (page - 1) * page_size
        return TrashPage(
            items=items[start : start + page_size],
            page=page,
            page_size=page_size,
            total=total,
            total_pages=(total + page_size - 1) // page_size if total else 0,
        )

    def get_trashed_item(
        session: Session,
        item_type: TrashType,
        item_id: str,
    ) -> Job | Resume | ScreeningRun | Evaluation:
        model = TRASH_MODELS[item_type]
        item = session.get(model, item_id)
        if not item or item.deleted_at is None:
            raise HTTPException(status_code=404, detail="回收站项目不存在")
        if not is_top_level_trash_item(item_type, item):
            raise HTTPException(status_code=404, detail="回收站项目不存在")
        return item

    def restore_trash_resource(
        session: Session,
        item_type: TrashType,
        item: Job | Resume | ScreeningRun | Evaluation,
    ) -> None:
        if item_type == "job":
            assert isinstance(item, Job)
            restore_job(item)
        elif item_type == "resume":
            assert isinstance(item, Resume)
            restore_resume(session, item)
        elif item_type == "run":
            assert isinstance(item, ScreeningRun)
            restore_run(item)
        else:
            assert isinstance(item, Evaluation)
            restore_evaluation(session, item)

    @app.post("/api/trash/{item_type}/{item_id}/restore")
    def restore_trash_item(
        item_type: TrashType,
        item_id: str,
        session: Session = Depends(get_session),
    ) -> dict[str, bool]:
        item = get_trashed_item(session, item_type, item_id)
        restore_trash_resource(session, item_type, item)
        session.commit()
        return {"restored": True}

    @app.delete("/api/trash/{item_type}/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
    def permanently_delete_trash_item(
        item_type: TrashType,
        item_id: str,
        session: Session = Depends(get_session),
    ) -> None:
        item = get_trashed_item(session, item_type, item_id)
        purge_item(session, runtime_settings, item_type, item)
        session.commit()

    @app.post("/api/trash/restore", response_model=BatchActionResult)
    def batch_restore_trash(
        payload: TrashBatchAction,
        session: Session = Depends(get_session),
    ) -> BatchActionResult:
        restored = 0
        for reference in payload.items:
            item = get_trashed_item(session, reference.item_type, reference.id)
            restore_trash_resource(session, reference.item_type, item)
            restored += 1
        session.commit()
        return BatchActionResult(updated_count=restored)

    @app.post("/api/trash/purge", response_model=BatchActionResult)
    def batch_purge_trash(
        payload: TrashBatchAction,
        session: Session = Depends(get_session),
    ) -> BatchActionResult:
        purged = 0
        for reference in payload.items:
            item = get_trashed_item(session, reference.item_type, reference.id)
            purge_item(session, runtime_settings, reference.item_type, item)
            purged += 1
        session.commit()
        return BatchActionResult(updated_count=purged)

    @app.delete("/api/trash", response_model=BatchActionResult)
    def empty_trash(session: Session = Depends(get_session)) -> BatchActionResult:
        purged = 0
        for item_type in ("job", "resume", "run", "evaluation"):
            model = TRASH_MODELS[item_type]
            items = list(session.scalars(select(model).where(model.deleted_at.is_not(None))).all())
            for item in items:
                if session.get(model, item.id) is None:
                    continue
                if not is_top_level_trash_item(item_type, item):
                    continue
                purge_item(session, runtime_settings, item_type, item)
                purged += 1
            session.flush()
        session.commit()
        return BatchActionResult(updated_count=purged)

    frontend_dist = Path.cwd() / "frontend" / "dist"
    if frontend_dist.is_dir():
        app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")

    return app


app = create_app()
