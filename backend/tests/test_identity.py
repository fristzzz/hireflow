"""验证身份提取：模型输出解析、正则兜底、待提取队列处理（全部 fake，不调用真实模型）。"""

from __future__ import annotations

import asyncio

from auth_helpers import authed_client
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import Settings
from app.identity import (
    IdentityInfo,
    extract_email,
    extract_phone,
    normalize_pasted_text,
    process_pending_identities,
    reconcile_identity,
)
from app.main import create_app
from app.models import Resume


class FakeIdentityExtractor:
    """按预设结果返回或抛错的 fake 提取器。"""

    def __init__(self, info: IdentityInfo | None = None, error: Exception | None = None) -> None:
        self.info = info or IdentityInfo()
        self.error = error
        self.calls: list[str] = []

    async def extract(self, *, resume_text: str) -> IdentityInfo:
        self.calls.append(resume_text)
        if self.error:
            raise self.error
        return self.info


def make_app(tmp_path, identity_extractor=None):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        data_dir=tmp_path / "data",
        imap_poll_seconds=0,
    )
    return create_app(settings=settings, identity_extractor=identity_extractor)


def test_normalize_pasted_text() -> None:
    assert normalize_pasted_text("  张三\r\n\r\n\r\n五年经验\r\n  ") == "张三\n\n五年经验"
    assert normalize_pasted_text(" \n\r\n ") == ""


def test_extract_email_and_phone() -> None:
    text = "联系方式：zhangsan@example.com，手机 138-0013-8000 不可用，电话 13912345678"
    assert extract_email(text) == "zhangsan@example.com"
    assert extract_phone(text) == "13912345678"
    assert extract_email("没有邮箱") is None
    assert extract_phone("010-12345678") is None


def test_reconcile_prefers_regex_over_model() -> None:
    """模型输出与正则冲突时以正则为准；正则未命中时采纳通过校验的模型值。"""

    text = "邮箱 real@example.com 手机 13912345678"
    info = reconcile_identity({"name": "张三", "email": "fake@other.com", "phone": "13800000000"}, text)
    assert info.email == "real@example.com"
    assert info.phone == "13912345678"
    assert info.name == "张三"

    info = reconcile_identity({"name": None, "email": " Model@Example.com ", "phone": "138-0013-8000"}, "无联系方式")
    assert info.email == "model@example.com"
    assert info.phone == "13800138000"
    assert info.name is None


def test_reconcile_drops_invalid_model_values() -> None:
    info = reconcile_identity({"name": "未识别", "email": "not-an-email", "phone": "123"}, "纯文本")
    assert info == IdentityInfo()


def test_process_pending_identities_marks_ready_and_backfills_name(tmp_path) -> None:
    """提取成功置 ready，并按 should_update_display_name 规则补 display_name。"""

    app = make_app(tmp_path)
    with authed_client(app) as client:
        created = client.post("/api/pool/resumes/paste", json={"text": "张三\n邮箱 zs@example.com"})
        assert created.json()["created"] is True
        resume_id = created.json()["resume"]["id"]

    extractor = FakeIdentityExtractor(IdentityInfo(name="张三", email="zs@example.com", phone="13912345678"))
    processed = asyncio.run(process_pending_identities(app.state.session_factory, extractor))
    assert processed == 1
    assert extractor.calls and "张三" in extractor.calls[0]

    with app.state.session_factory() as session:
        resume = session.get(Resume, resume_id)
        assert resume.identity_status == "ready"
        assert resume.identity_name == "张三"
        assert resume.identity_email == "zs@example.com"
        assert resume.identity_phone == "13912345678"
        # 原 display_name 是自动生成的文件名，应被真实姓名覆盖
        assert resume.display_name == "张三"


def test_process_pending_identities_keeps_manual_name(tmp_path) -> None:
    """用户粘贴时填写的姓名是真实姓名，不被模型结果覆盖。"""

    app = make_app(tmp_path)
    with authed_client(app) as client:
        created = client.post("/api/pool/resumes/paste", json={"text": "工作经历……", "name": "李四"})
        resume_id = created.json()["resume"]["id"]

    extractor = FakeIdentityExtractor(IdentityInfo(name="王五"))
    asyncio.run(process_pending_identities(app.state.session_factory, extractor))
    with app.state.session_factory() as session:
        resume = session.get(Resume, resume_id)
        assert resume.identity_status == "ready"
        assert resume.identity_name == "王五"
        assert resume.display_name == "李四"


def test_process_pending_identities_marks_failed_on_error(tmp_path) -> None:
    app = make_app(tmp_path)
    with authed_client(app) as client:
        created = client.post("/api/pool/resumes/paste", json={"text": "任意简历文本"})
        resume_id = created.json()["resume"]["id"]

    extractor = FakeIdentityExtractor(error=RuntimeError("模型超时"))
    processed = asyncio.run(process_pending_identities(app.state.session_factory, extractor))
    assert processed == 0
    with app.state.session_factory() as session:
        assert session.get(Resume, resume_id).identity_status == "failed"


def test_process_pending_identities_skips_unparsed_resume(tmp_path) -> None:
    """解析文本尚未就绪的简历保留 pending，等待下一轮。"""

    app = make_app(tmp_path)
    with authed_client(app):
        with app.state.session_factory() as session:
            session.add(
                Resume(
                    original_name="待解析.pdf",
                    mime_type="application/pdf",
                    storage_path="/tmp/none.pdf",
                    sha256="a" * 64,
                    size_bytes=1,
                    parse_status="parsing",
                    parsed_text=None,
                    source="email",
                    identity_status="pending",
                )
            )
            session.commit()

    extractor = FakeIdentityExtractor(IdentityInfo(name="张三"))
    processed = asyncio.run(process_pending_identities(app.state.session_factory, extractor))
    assert processed == 0
    assert extractor.calls == []
    with app.state.session_factory() as session:
        resume = session.scalar(select(Resume))
        assert resume.identity_status == "pending"
