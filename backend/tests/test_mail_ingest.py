"""验证邮箱简历捕获：附件进入简历池、全库去重、跳过与手动同步接口。"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pymupdf as fitz
from auth_helpers import authed_client
from fastapi.testclient import TestClient

from app.documents import DocumentProcessor
from mail_helpers import (
    DOCX_SUBTYPE,
    FakeIMAPClient,
    docx_attachment,
    install_fake_imap,
    make_docx,
    make_email,
    make_mail_app,
)


def make_pdf() -> bytes:
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "Resume Experience Education Skills")
    content = document.tobytes()
    document.close()
    return content


def pdf_attachment(filename: str) -> tuple[str, bytes, str, str]:
    return (filename, make_pdf(), "application", "pdf")


def pool_items(client: TestClient) -> list[dict]:
    return client.get("/api/pool/resumes").json()


def create_job(client: TestClient, name: str) -> str:
    created = client.post(
        "/api/jobs",
        json={"name": name, "jd_text": "负责数据平台建设", "prompt": "考察工程能力"},
    )
    assert created.status_code == 201
    return created.json()["id"]


def test_extract_source_title_rules() -> None:
    """从文件名/主题提取职位名：优先文件名括号段，剥城市薪资，无括号不猜。"""

    from app.mail_ingest import MailIngester

    assert (
        MailIngester.extract_source_title("【资深JAVA工程师-实时数据引擎_深圳_35-55K】吴先生_11年.pdf", "邮件主题")
        == "资深JAVA工程师-实时数据引擎"
    )
    assert MailIngester.extract_source_title("【测试工程师_深圳_28-50K】沈标_12年.pdf", "") == "测试工程师"
    # 缺左括号的残缺文件名（无职位段分隔符）不产出职位
    assert MailIngester.extract_source_title("候选人A-张三.docx", "主题") is None
    # 主题括号段作为兜底
    assert (
        MailIngester.extract_source_title("候选人A.docx", "【数据架构专家_深圳_35-55K】-张三")
        == "数据架构专家"
    )


def test_email_sync_imports_attachments_into_pool(tmp_path, monkeypatch) -> None:
    """附件应进入简历池，不直接落到任何岗位。"""

    install_fake_imap(
        monkeypatch,
        [
            make_email(
                "应聘 数据架构师 简历请查收",
                [docx_attachment("张三-数据架构师.docx"), ("logo.png", b"\x89PNG" * 8, "image", "png")],
            )
        ],
    )
    app = make_mail_app(tmp_path)

    with authed_client(app) as client:
        job_id = create_job(client, "资深数据架构师")
        result = client.post("/api/email/sync")

        assert result.status_code == 200
        body = result.json()
        assert body["messages"] == 1
        assert body["imported"] == 1
        assert body["skipped"] == 0  # 图片附件在提取阶段被静默过滤
        assert body["failed"] == 0

        assert client.get(f"/api/jobs/{job_id}/resumes").json() == []  # 岗位内无简历
        items = pool_items(client)
        assert len(items) == 1
        assert items[0]["source"] == "email"
        assert items[0]["job_id"] is None
        assert items[0]["parse_status"] == "ready"
        assert items[0]["evaluation"] is None


def test_email_sync_skips_duplicate_already_in_job(tmp_path, monkeypatch) -> None:
    """同一份简历已存在于某个岗位时，邮箱再收取按重复处理，不入池。"""

    content = make_docx()
    app = make_mail_app(tmp_path)
    with authed_client(app) as client:
        job_id = create_job(client, "资深数据架构师")
        uploaded = client.post(
            f"/api/jobs/{job_id}/resumes",
            files=[("files", ("候选人.docx", content, DOCX_SUBTYPE))],
        )
        assert uploaded.json()["uploaded"]

        install_fake_imap(monkeypatch, [make_email("简历附件", [docx_attachment("候选人.docx", content)])])
        result = client.post("/api/email/sync")
        assert result.json()["imported"] == 0
        assert result.json()["duplicates"] == 1
        assert pool_items(client) == []


def test_email_sync_skips_duplicate_within_pool(tmp_path, monkeypatch) -> None:
    """相同内容两次投递只入池一次。"""

    same = make_docx()
    install_fake_imap(
        monkeypatch,
        [
            make_email("简历 A", [docx_attachment("候选人A.docx", same)]),
            make_email("简历 B", [docx_attachment("候选人B.docx", same)]),
        ],
    )
    app = make_mail_app(tmp_path)

    with authed_client(app) as client:
        result = client.post("/api/email/sync")
        assert result.json()["imported"] == 1
        assert result.json()["duplicates"] == 1
        assert len(pool_items(client)) == 1


def test_email_sync_pdf_goes_through_async_parse(tmp_path, monkeypatch) -> None:
    """PDF 附件入池后应进入异步解析流程。"""

    async def fake_extract_text(self, storage_path):
        return "[第 1 页]\n候选人具有分布式系统经验"

    monkeypatch.setattr(DocumentProcessor, "extract_text", fake_extract_text)
    install_fake_imap(monkeypatch, [make_email("简历", [pdf_attachment("候选人.pdf")])])
    app = make_mail_app(tmp_path)

    with authed_client(app) as client:
        result = client.post("/api/email/sync")
        assert result.json()["imported"] == 1

        for _ in range(50):
            resume = pool_items(client)[0]
            if resume["parse_status"] == "ready":
                break
            time.sleep(0.05)
        assert resume["parse_status"] == "ready"
        assert client.get(f"/api/resumes/{resume['id']}/text").status_code == 200


def test_email_sync_keeps_job_title_from_attachment_filename(tmp_path, monkeypatch) -> None:
    """附件文件名带的职位段应存入简历并供简历池展示。"""

    install_fake_imap(
        monkeypatch,
        [make_email("简历投递", [docx_attachment("【资深JAVA工程师-实时数据引擎_深圳_35-55K】高成东.docx")])],
    )
    app = make_mail_app(tmp_path)

    with authed_client(app) as client:
        result = client.post("/api/email/sync")
        assert result.json()["imported"] == 1
        item = client.get("/api/pool/resumes").json()[0]
        assert item["source_title"] == "资深JAVA工程师-实时数据引擎"


def test_email_sync_keeps_job_title_from_subject_bracket(tmp_path, monkeypatch) -> None:
    """文件名无职位段时，主题括号段的职位名作为兜底。"""

    install_fake_imap(monkeypatch, [make_email("【数据架构专家_深圳_35-55K】候选人", [docx_attachment("候选人.docx")])])
    app = make_mail_app(tmp_path)

    with authed_client(app) as client:
        client.post("/api/email/sync")
        item = client.get("/api/pool/resumes").json()[0]
        assert item["source_title"] == "数据架构专家"


def test_email_sync_without_attachments_marks_message_seen(tmp_path, monkeypatch) -> None:
    """无附件邮件直接标记已读，不计入导入。"""

    install_fake_imap(monkeypatch, [make_email("普通问候邮件", [])])
    app = make_mail_app(tmp_path)

    with authed_client(app) as client:
        result = client.post("/api/email/sync")
        assert result.json()["skipped"] == 1
        assert result.json()["imported"] == 0
        assert FakeIMAPClient.seen == {b"1"}


def test_email_sync_requires_configuration(tmp_path) -> None:
    """未配置 IMAP 时手动同步返回 400，状态接口显示未配置。"""

    app = make_mail_app(tmp_path, configured=False)
    with authed_client(app) as client:
        response = client.post("/api/email/sync")
        assert response.status_code == 400
        assert client.get("/api/email/status").json()["configured"] is False


def test_email_status_reports_last_run(tmp_path, monkeypatch) -> None:
    install_fake_imap(monkeypatch, [make_email("简历", [docx_attachment("候选人.docx")])])
    app = make_mail_app(tmp_path)

    with authed_client(app) as client:
        before = client.get("/api/email/status").json()
        assert before["configured"] is True
        assert before["last_result"] is None

        client.post("/api/email/sync")
        after = client.get("/api/email/status").json()
        assert after["configured"] is True
        assert after["mailbox"] == "hr@tapdata.io"
        assert after["last_result"]["imported"] == 1


def test_migration_0005_sets_default_source_on_existing_resumes(tmp_path, monkeypatch) -> None:
    """迁移 0005 升级后，旧简历的 source 应默认为 upload。"""

    from alembic import command
    from alembic.config import Config

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'mig.db'}")
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parents[1] / "alembic"))

    connection = sqlite3.connect(tmp_path / "mig.db")
    connection.execute(
        "CREATE TABLE resumes ("
        "id VARCHAR(36) PRIMARY KEY, job_id VARCHAR(36) NOT NULL, "
        "original_name VARCHAR(255) NOT NULL, mime_type VARCHAR(120) NOT NULL, "
        "storage_path TEXT NOT NULL, sha256 VARCHAR(64) NOT NULL, "
        "size_bytes INTEGER NOT NULL, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL)"
    )
    connection.execute(
        "INSERT INTO resumes (id, job_id, original_name, mime_type, storage_path, sha256, size_bytes, created_at, updated_at) "
        "VALUES ('resume-1', 'job-1', '旧简历.pdf', 'application/pdf', '/tmp/x.pdf', 'abc', 1, '2026-01-01', '2026-01-01')"
    )
    connection.commit()
    connection.close()

    command.stamp(config, "20260829_0004")
    command.upgrade(config, "20260903_0007")

    connection = sqlite3.connect(tmp_path / "mig.db")
    source = connection.execute("SELECT source FROM resumes WHERE id = 'resume-1'").fetchone()[0]
    connection.close()
    assert source == "upload"


def test_failed_email_remains_unread_and_retries(tmp_path, monkeypatch) -> None:
    """读取邮件不会提前置已读，处理失败后下轮仍能导入。"""
    install_fake_imap(monkeypatch, [make_email("合成测试邮件", [docx_attachment("synthetic.docx")])])
    app = make_mail_app(tmp_path)
    with authed_client(app) as client:
        original = app.state.mail_ingester._process_message

        async def fail_once(raw, summary):
            raise RuntimeError("合成测试故障")

        monkeypatch.setattr(app.state.mail_ingester, "_process_message", fail_once)
        assert client.post("/api/email/sync").json()["failed"] == 1
        assert FakeIMAPClient.seen == set()
        monkeypatch.setattr(app.state.mail_ingester, "_process_message", original)
        assert client.post("/api/email/sync").json()["imported"] == 1
        assert FakeIMAPClient.seen == {b"1"}
