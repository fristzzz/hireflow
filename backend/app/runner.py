"""持久化并发筛选任务并隔离单份简历失败。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .config import Settings
from .documents import DocumentProcessor
from .errors import public_failure_message
from .evaluator import Evaluator
from .models import Evaluation, Resume, ScreeningRun
from .names import should_update_display_name


def now() -> datetime:
    return datetime.now(timezone.utc)


ACTIVE_EVALUATION_STATUSES = frozenset({"queued", "parsing", "evaluating"})
ACTIVE_RUN_STATUSES = frozenset({"queued", "running"})


class ScreeningRunner:
    """以运行 ID 为接口管理并发、恢复、取消和结果落库。"""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        processor: DocumentProcessor,
        evaluator: Evaluator,
        settings: Settings,
    ) -> None:
        self.session_factory = session_factory
        self.processor = processor
        self.evaluator = evaluator
        self.settings = settings
        self.tasks: dict[str, asyncio.Task[None]] = {}
        self._cancel_requested: set[str] = set()

    def recover_interrupted(self) -> None:
        with self.session_factory() as session:
            runs = session.scalars(
                select(ScreeningRun).where(
                    ScreeningRun.deleted_at.is_(None),
                    ScreeningRun.status.in_(list(ACTIVE_RUN_STATUSES)),
                )
            ).all()
            for run in runs:
                run.status = "failed"
                run.completed_at = now()
                for evaluation in run.evaluations:
                    if evaluation.status in ACTIVE_EVALUATION_STATUSES:
                        evaluation.status = "failed"
                        evaluation.error = "应用重启导致任务中断，可点击重试"
                session.commit()

    def start(self, run_id: str) -> None:
        self._cancel_requested.discard(run_id)
        task = asyncio.create_task(self._execute(run_id))
        self.tasks[run_id] = task
        task.add_done_callback(lambda _: self.tasks.pop(run_id, None))

    def cancel(self, run_id: str) -> bool:
        """请求取消运行：先落库终态，再取消内存任务，避免完成态覆盖取消。"""

        with self.session_factory() as session:
            run = session.get(ScreeningRun, run_id)
            if not run or run.deleted_at is not None:
                return False
            if run.status not in ACTIVE_RUN_STATUSES and run_id not in self.tasks:
                return False
            self._mark_run_canceled(session, run)
            session.commit()

        self._cancel_requested.add(run_id)
        task = self.tasks.get(run_id)
        if task and not task.done():
            task.cancel()
        return True

    def _mark_run_canceled(self, session: Session, run: ScreeningRun) -> None:
        run.status = "canceled"
        run.completed_at = run.completed_at or now()
        run.completed_count = sum(item.status == "completed" for item in run.evaluations)
        run.failed_count = sum(item.status == "failed" for item in run.evaluations)
        for evaluation in run.evaluations:
            if evaluation.status in ACTIVE_EVALUATION_STATUSES:
                evaluation.status = "canceled"
                evaluation.error = None

    def is_cancel_requested(self, run_id: str) -> bool:
        return run_id in self._cancel_requested

    async def shutdown(self) -> None:
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _run_is_canceled(self, run_id: str) -> bool:
        if run_id in self._cancel_requested:
            return True
        with self.session_factory() as session:
            run = session.get(ScreeningRun, run_id)
            return bool(run and run.status == "canceled")

    def promote_current(self, session: Session, evaluation: Evaluation) -> None:
        """在同一事务内把评估提升为当前结果，并清掉同简历其他 current。

        先全部清零并 flush，再点亮目标，避免唯一索引在瞬时双 True 时冲突。
        """

        siblings = list(
            session.scalars(
                select(Evaluation)
                .where(
                    Evaluation.resume_id == evaluation.resume_id,
                    Evaluation.deleted_at.is_(None),
                )
                .with_for_update()
            ).all()
        )
        for item in siblings:
            if item.is_current:
                item.is_current = False
        session.flush()
        evaluation.is_current = True
        session.flush()

    async def _execute(self, run_id: str) -> None:
        try:
            with self.session_factory() as session:
                run = session.get(ScreeningRun, run_id)
                if not run:
                    return
                if run.status == "canceled" or run_id in self._cancel_requested:
                    self._mark_run_canceled(session, run)
                    session.commit()
                    return
                run.status = "running"
                run.started_at = run.started_at or now()
                evaluation_ids = [
                    item.id for item in run.evaluations if item.status in {"queued", "failed"}
                ]
                session.commit()

            semaphore = asyncio.Semaphore(max(1, self.settings.max_concurrency))

            async def guarded(evaluation_id: str) -> None:
                async with semaphore:
                    if self._run_is_canceled(run_id):
                        with self.session_factory() as session:
                            evaluation = session.get(Evaluation, evaluation_id)
                            if evaluation and evaluation.status in ACTIVE_EVALUATION_STATUSES | {"failed", "queued"}:
                                if evaluation.status != "completed":
                                    evaluation.status = "canceled"
                                    evaluation.error = None
                                    session.commit()
                        return
                    await self._execute_one(evaluation_id, run_id)

            try:
                await asyncio.gather(*(guarded(item_id) for item_id in evaluation_ids))
            except asyncio.CancelledError:
                with self.session_factory() as session:
                    run = session.get(ScreeningRun, run_id)
                    if run:
                        self._mark_run_canceled(session, run)
                        session.commit()
                return

            with self.session_factory() as session:
                run = session.get(ScreeningRun, run_id)
                if not run:
                    return
                run.completed_count = sum(item.status == "completed" for item in run.evaluations)
                run.failed_count = sum(item.status == "failed" for item in run.evaluations)
                if run.status == "canceled" or run_id in self._cancel_requested:
                    self._mark_run_canceled(session, run)
                else:
                    run.status = "completed" if run.failed_count == 0 else "partial"
                    run.completed_at = now()
                session.commit()
        finally:
            self._cancel_requested.discard(run_id)

    async def _execute_one(self, evaluation_id: str, run_id: str) -> None:
        try:
            if self._run_is_canceled(run_id):
                with self.session_factory() as session:
                    evaluation = session.get(Evaluation, evaluation_id)
                    if evaluation and evaluation.status in ACTIVE_EVALUATION_STATUSES | {"queued", "failed"}:
                        evaluation.status = "canceled"
                        evaluation.error = None
                        session.commit()
                return

            with self.session_factory() as session:
                evaluation = session.get(Evaluation, evaluation_id)
                if not evaluation:
                    return
                if evaluation.status == "canceled":
                    return
                evaluation.status = "parsing"
                evaluation.error = None
                evaluation.attempts += 1
                session.commit()
                resume_id = evaluation.resume_id

            with self.session_factory() as session:
                resume = session.get(Resume, resume_id)
                run = session.get(ScreeningRun, run_id)
                if not resume or not run:
                    raise RuntimeError("筛选关联数据不存在")
                if run.status == "canceled":
                    evaluation_row = session.get(Evaluation, evaluation_id)
                    if evaluation_row and evaluation_row.status in ACTIVE_EVALUATION_STATUSES:
                        evaluation_row.status = "canceled"
                        session.commit()
                    return
                resume_text = resume.parsed_text
                storage_path = resume.storage_path
                jd_text = run.jd_snapshot
                prompt = run.prompt_snapshot

            if not resume_text:
                resume_text = await self.processor.extract_text(storage_path)
                if self._run_is_canceled(run_id):
                    with self.session_factory() as session:
                        evaluation = session.get(Evaluation, evaluation_id)
                        if evaluation and evaluation.status in ACTIVE_EVALUATION_STATUSES:
                            evaluation.status = "canceled"
                            evaluation.error = None
                            session.commit()
                    return
                with self.session_factory() as session:
                    resume = session.get(Resume, resume_id)
                    if resume:
                        resume.parsed_text = resume_text
                        resume.parsed_text_source = "extracted"
                        resume.parsed_text_updated_at = now()
                        resume.parse_status = "ready"
                        resume.parse_error = None
                        session.commit()

            with self.session_factory() as session:
                evaluation = session.get(Evaluation, evaluation_id)
                run = session.get(ScreeningRun, run_id)
                if not evaluation:
                    return
                if not run or run.status == "canceled" or self.is_cancel_requested(run_id):
                    evaluation.status = "canceled"
                    evaluation.error = None
                    session.commit()
                    return
                evaluation.status = "evaluating"
                session.commit()

            result = await self.evaluator.evaluate(jd_text=jd_text, prompt=prompt, resume_text=resume_text)

            with self.session_factory() as session:
                evaluation = session.get(Evaluation, evaluation_id)
                run = session.get(ScreeningRun, run_id)
                if not evaluation:
                    return
                if not run or run.status == "canceled" or self.is_cancel_requested(run_id):
                    evaluation.status = "canceled"
                    evaluation.error = None
                    session.commit()
                    return
                evaluation.result_json = result.model_dump(mode="json")
                evaluation.status = "completed"
                evaluation.error = None
                resume = session.get(Resume, evaluation.resume_id)
                extracted_name = result.candidate.name
                if resume and should_update_display_name(resume.display_name, resume.original_name, extracted_name):
                    resume.display_name = extracted_name.strip()
                self.promote_current(session, evaluation)
                session.commit()
        except asyncio.CancelledError:
            with self.session_factory() as session:
                evaluation = session.get(Evaluation, evaluation_id)
                if evaluation and evaluation.status in ACTIVE_EVALUATION_STATUSES:
                    evaluation.status = "canceled"
                    evaluation.error = None
                    session.commit()
            raise
        except Exception as exc:
            if self._run_is_canceled(run_id):
                with self.session_factory() as session:
                    evaluation = session.get(Evaluation, evaluation_id)
                    if evaluation and evaluation.status in ACTIVE_EVALUATION_STATUSES:
                        evaluation.status = "canceled"
                        evaluation.error = None
                        session.commit()
                return
            with self.session_factory() as session:
                evaluation = session.get(Evaluation, evaluation_id)
                if evaluation:
                    evaluation.status = "failed"
                    evaluation.error = public_failure_message(exc, "评估失败，请重试")
                    session.commit()
