"""从招聘邮箱捕获简历附件并进入简历池（不直接分配岗位）。

只处理未读邮件：导入成功或无有效附件的邮件标记为已读；
处理过程中出错的邮件保持未读，下一轮轮询自动重试。
简历先入池（job_id 为空），由用户在简历池中分配岗位并评估。
不记录邮件正文与附件内容，日志只保留计数信息（简历属于不可信个人信息）。
"""

from __future__ import annotations

import asyncio
import imaplib
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from email import policy
from email.message import Message
from email.parser import BytesParser
from pathlib import Path
import re
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from .config import Settings
from .documents import DocumentError, DocumentProcessor
from .models import Resume
from .names import display_name_from_filename
from .workbench import utc_now

logger = logging.getLogger(__name__)

_ATTACHMENT_MIME_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/octet-stream",
}

# 招聘平台简历文件名常见的「职位_城市_薪资」后缀，用于剥掉后保留干净职位名
_CITY_NAMES = (
    "北京|上海|广州|深圳|杭州|成都|南京|武汉|苏州|西安|长沙|天津|重庆|佛山|东莞|郑州|青岛|厦门|福州|合肥|济南|宁波|"
    "无锡|珠海|中山|大连|昆明|南昌|太原|石家庄|沈阳|哈尔滨|长春|南宁|贵阳|兰州|乌鲁木齐|海口|三亚|常州|徐州|南通|温州|"
    "嘉兴|绍兴|泉州|烟台|潍坊|常州|惠州|廊坊"
)
_SALARY_PART = r"\d+(?:\.\d+)?\s*[Kk万]?(?:\s*[-~—～至]\s*\d+(?:\.\d+)?\s*[Kk万])?"
_LOCATION_OR_SALARY_SUFFIX = re.compile(
    rf"_(?:{_CITY_NAMES})(?:_?{_SALARY_PART})?$|_{_SALARY_PART}$"
)
_BRACKET_PAIR = re.compile(r"[【\[](?P<title>[^】\]]+)[】\]]")
_TRAILING_JUNK = re.compile(r"[_ \u3000\-–—|｜,，\)）\]】:：·]+$")


@dataclass
class SyncSummary:
    """一次邮箱同步的结果计数。"""

    messages: int = 0
    imported: int = 0
    duplicates: int = 0
    skipped: int = 0
    failed: int = 0
    started_at: datetime = field(default_factory=utc_now)
    finished_at: datetime | None = None
    error: str | None = None


class MailIngester:
    """封装 IMAP 拉取、附件过滤、职位提取与入库。"""

    def __init__(
        self,
        settings: Settings,
        session_factory: sessionmaker,
        document_processor: DocumentProcessor,
        parse_callback: Callable[[str], None],
    ) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.document_processor = document_processor
        # 注入上传流程的解析入口（通常是调度 parse_uploaded_resume 的闭包）
        self.parse_callback = parse_callback
        self.last_summary: SyncSummary | None = None

    async def sync(self) -> SyncSummary:
        """拉取一轮未读邮件并把简历附件导入岗位。"""
        summary = SyncSummary()
        try:
            messages = await asyncio.to_thread(self._fetch_unseen)
        except Exception as exc:
            summary.error = f"邮箱连接或拉取失败：{type(exc).__name__}"
            summary.finished_at = utc_now()
            self.last_summary = summary
            logger.warning("邮箱同步失败：%s", type(exc).__name__)
            return summary

        summary.messages = len(messages)
        seen_uids: list[bytes] = []
        for uid, raw in messages:
            try:
                processed = await self._process_message(raw, summary)
            except Exception:
                summary.failed += 1
                logger.exception("处理邮件失败，保持未读等待重试")
                continue
            if processed:
                seen_uids.append(uid)

        if seen_uids:
            try:
                await asyncio.to_thread(self._mark_seen, seen_uids)
            except Exception:
                logger.exception("标记邮件已读失败，下一轮将依赖内容去重")

        summary.finished_at = utc_now()
        self.last_summary = summary
        logger.info(
            "邮箱同步完成：邮件 %d，导入 %d，重复 %d，跳过 %d，失败 %d",
            summary.messages,
            summary.imported,
            summary.duplicates,
            summary.skipped,
            summary.failed,
        )
        return summary

    # ---- IMAP 阻塞操作（在线程中执行） ----

    def _connect(self) -> imaplib.IMAP4_SSL:
        client = imaplib.IMAP4_SSL(self.settings.imap_host, self.settings.imap_port)
        client.login(self.settings.imap_user, self.settings.imap_password)
        client.select(self.settings.imap_mailbox)
        return client

    def _fetch_unseen(self) -> list[tuple[bytes, bytes]]:
        client = self._connect()
        try:
            _, data = client.uid("search", None, "UNSEEN")
            uids = data[0].split()
            messages: list[tuple[bytes, bytes]] = []
            for uid in uids:
                _, fetched = client.uid("fetch", uid, "(BODY.PEEK[])")
                if fetched and isinstance(fetched[0], tuple):
                    messages.append((uid, fetched[0][1]))
            return messages
        finally:
            client.logout()

    def _mark_seen(self, uids: list[bytes]) -> None:
        client = self._connect()
        try:
            for uid in uids:
                client.uid("store", uid, "+FLAGS", "(\\Seen)")
        finally:
            client.logout()

    # ---- 邮件处理 ----

    async def _process_message(self, raw: bytes, summary: SyncSummary) -> bool:
        """处理单封邮件；返回是否可以标记已读。"""
        message = BytesParser(policy=policy.default).parsebytes(raw)
        subject = message.get("Subject") or ""
        attachments = list(self._iter_resume_attachments(message))
        if not attachments:
            summary.skipped += 1
            return True

        with self.session_factory() as session:
            imported_ids: list[str] = []
            for filename, mime_type, content in attachments:
                resume_id = str(uuid4())
                try:
                    ingested = self.document_processor.ingest(
                        job_id=None,
                        resume_id=resume_id,
                        filename=filename,
                        mime_type=mime_type,
                        content=content,
                    )
                except DocumentError:
                    summary.skipped += 1
                    continue
                duplicate = session.scalar(
                    select(Resume)
                    .where(Resume.sha256 == ingested.sha256, Resume.deleted_at.is_(None))
                    .limit(1)
                )
                if duplicate:
                    Path(ingested.storage_path).unlink(missing_ok=True)
                    if ingested.preview_path:
                        Path(ingested.preview_path).unlink(missing_ok=True)
                    summary.duplicates += 1
                    continue
                resume = Resume(
                    id=resume_id,
                    display_name=display_name_from_filename(ingested.original_name) or ingested.original_name,
                    parsed_text_source="extracted",
                    parsed_text_updated_at=utc_now() if ingested.parsed_text else None,
                    source="email",
                    source_title=self.extract_source_title(ingested.original_name, subject),
                    identity_status="pending",
                    **ingested.__dict__,
                )
                session.add(resume)
                session.commit()
                summary.imported += 1
                imported_ids.append(resume.id)

        for resume_id in imported_ids:
            # 回调内部会自行跳过已有解析文本的简历（如 DOCX 已在入库时解析）
            self.parse_callback(resume_id)
        return True

    def _iter_resume_attachments(self, message: Message):
        """遍历邮件中的 PDF/DOCX 附件，产出 (文件名, MIME, 内容)。"""
        for part in message.walk():
            if part.get_content_maintype() == "multipart":
                continue
            filename = part.get_filename()
            disposition = part.get_content_disposition()
            if not filename or disposition not in {"attachment", "inline"}:
                continue
            extension = Path(filename).suffix.lower()
            if extension not in DocumentProcessor.allowed_extensions:
                continue
            content_type = part.get_content_type()
            if content_type not in _ATTACHMENT_MIME_TYPES:
                continue
            content = part.get_payload(decode=True)
            if content:
                yield filename, content_type, content

    @staticmethod
    def extract_source_title(filename: str, subject: str) -> str | None:
        """从招聘平台简历文件名或邮件主题提取职位名。

        文件名常见形态为「【职位_城市_薪资】姓名_年限.pdf」，主题也可能带「【职位】」；
        只信任带括号分隔的职位段，避免把候选人姓名或泛化文案当作职位。
        优先文件名，其次主题括号段，清洗掉城市/薪资后缀后返回。
        """

        head = Path(filename).name
        candidates: list[str] = []
        bracket = _BRACKET_PAIR.search(head)
        if bracket:
            candidates.append(bracket.group("title"))
        elif "】" in head or "]" in head:
            candidates.append(head.split("】", 1)[0].split("]", 1)[0])
        subject_bracket = _BRACKET_PAIR.search(subject or "")
        if subject_bracket:
            candidates.append(subject_bracket.group("title"))
        for raw in candidates:
            title = MailIngester._clean_source_title(raw)
            if title:
                return title
        return None

    @staticmethod
    def _clean_source_title(raw: str) -> str | None:
        text = (raw or "").strip()
        if not text:
            return None
        for extension in (".pdf", ".docx"):
            if text.lower().endswith(extension):
                text = text[: -len(extension)]
        text = text.strip(" \u3000【】『』〔〕[]（()）")
        text = _LOCATION_OR_SALARY_SUFFIX.sub("", text)
        text = text.strip(" _\u3000-–—|｜,，·:：）)")
        return text or None
