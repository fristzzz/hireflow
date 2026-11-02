"""验证简历池：列表与评估摘要、分配岗位、分配并评估、去重与迁移 0006。"""

from __future__ import annotations

import sqlite3
from io import BytesIO
from pathlib import Path

from docx import Document
from auth_helpers import authed_client
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.schemas import EvaluationCard
from mail_helpers import DOCX_SUBTYPE, ingest_one_email_into_pool, make_docx


class FakeEvaluator:
    async def evaluate(self, *, jd_text: str, prompt: str, resume_text: str, scoring_profile=None) -> EvaluationCard:
        return EvaluationCard.model_validate(
            {
                "candidate": {"name": "候选人", "gender": "female", "age": 28, "current_role": "数据工程师", "years_experience": 5},
                "overall_score": 86,
                "match_level": "high",
                "confidence": 0.9,
                "summary": "经历与岗位高度匹配。",
                "criteria": [
                    {
                        "criterion": "数据平台经验",
                        "score": 92,
                        "status": "met",
                        "evidence": [{"text": "五年数据平台经验", "location": "段落 1"}],
                    }
                ],
                "strengths": ["数据平台经验扎实"],
                "gaps": [],
                "risk_flags": [],
                "interview_questions": ["如何设计实时链路？"],
            }
        )


def make_app(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        data_dir=tmp_path / "data",
        imap_host="imap.example.com",
        imap_user="hr@tapdata.io",
        imap_password="secret",
        imap_poll_seconds=0,
    )
    return create_app(settings=settings, evaluator=FakeEvaluator())


def make_pdf_docx() -> bytes:
    document = Document()
    document.add_paragraph("张晨\n五年数据平台经验，熟悉 Flink 与 Kafka。")
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def create_job(client: TestClient, name: str = "资深数据架构师") -> str:
    created = client.post(
        "/api/jobs",
        json={
            "name": name,
            "jd_text": "负责实时数据平台",
            "prompt": "考察数据平台与流处理能力",
        },
    )
    assert created.status_code == 201
    return created.json()["id"]


def pool_items(client: TestClient) -> list[dict]:
    return client.get("/api/pool/resumes").json()


def test_pool_lists_email_resumes_even_after_assignment(tmp_path, monkeypatch) -> None:
    """已分配岗位的邮箱简历仍保留在池中，并带上岗位名与评估摘要。"""

    app = make_app(tmp_path)
    with authed_client(app) as client:
        job_id = create_job(client)
        entry = ingest_one_email_into_pool(client, monkeypatch)

        assert entry["job_id"] is None
        assert entry["evaluation"] is None

        result = client.post(
            "/api/pool/assign",
            json={"resume_ids": [entry["id"]], "job_id": job_id, "evaluate": True},
        )
        assert result.status_code == 200
        assert result.json()["assigned"] == 1
        assert result.json()["skipped"] == []
        run = result.json()["run"]
        assert run is not None
        assert run["total_count"] == 1

        # 简历出现在岗位下
        resumes = client.get(f"/api/jobs/{job_id}/resumes").json()
        assert len(resumes) == 1
        assert resumes[0]["source"] == "email"

        # 池中仍可见，job_name 与评估摘要就位
        item = pool_items(client)[0]
        assert item["job_id"] == job_id
        assert item["job_name"] == "资深数据架构师"
        assert item["evaluation"]["overall_score"] == 86
        assert item["evaluation"]["match_level"] == "high"


def test_pool_assign_without_evaluate_creates_no_run(tmp_path, monkeypatch) -> None:
    app = make_app(tmp_path)
    with authed_client(app) as client:
        job_id = create_job(client)
        entry = ingest_one_email_into_pool(client, monkeypatch)

        result = client.post(
            "/api/pool/assign",
            json={"resume_ids": [entry["id"]], "job_id": job_id, "evaluate": False},
        )
        assert result.status_code == 200
        body = result.json()
        assert body["assigned"] == 1
        assert body["run"] is None
        assert client.get(f"/api/jobs/{job_id}/runs").json()["total"] == 0


def test_pool_assign_skips_duplicate_in_target_job(tmp_path, monkeypatch) -> None:
    """目标岗位已有相同内容简历时跳过，并给出原因。"""

    content = make_docx()
    app = make_app(tmp_path)
    with authed_client(app) as client:
        job_id = create_job(client)

        from mail_helpers import docx_attachment, install_fake_imap, make_email

        install_fake_imap(monkeypatch, [make_email("简历", [docx_attachment("邮件版.docx", content)])])
        assert client.post("/api/email/sync").json()["imported"] == 1
        entry = pool_items(client)[0]

        # 岗位内手动上传同一份内容（岗位内去重只看本岗位，允许存在）
        upload = client.post(
            f"/api/jobs/{job_id}/resumes",
            files=[("files", ("已在岗.docx", content, DOCX_SUBTYPE))],
        )
        assert upload.json()["uploaded"]

        result = client.post(
            "/api/pool/assign",
            json={"resume_ids": [entry["id"]], "job_id": job_id, "evaluate": False},
        )
        body = result.json()
        assert body["assigned"] == 0
        assert len(body["skipped"]) == 1
        assert "相同内容" in body["skipped"][0]["reason"]


def test_pool_assign_rejects_unknown_job(tmp_path, monkeypatch) -> None:
    app = make_app(tmp_path)
    with authed_client(app) as client:
        entry = ingest_one_email_into_pool(client, monkeypatch)
        result = client.post(
            "/api/pool/assign",
            json={"resume_ids": [entry["id"]], "job_id": "不存在", "evaluate": False},
        )
        assert result.status_code == 404


def test_manually_uploaded_resume_not_in_pool(tmp_path) -> None:
    """手动上传进岗位的简历不属于简历池。"""

    app = make_app(tmp_path)
    with authed_client(app) as client:
        job_id = create_job(client)
        client.post(
            f"/api/jobs/{job_id}/resumes",
            files=[("files", ("手动上传.docx", make_pdf_docx(), DOCX_SUBTYPE))],
        )
        assert pool_items(client) == []


def test_pool_scope_all_lists_every_resume_with_filters_fields(tmp_path, monkeypatch) -> None:
    """scope=all 返回全库简历（含手动上传），并附评估岗位与候选人年龄、性别。"""

    app = make_app(tmp_path)
    with authed_client(app) as client:
        job_id = create_job(client)
        entry = ingest_one_email_into_pool(client, monkeypatch)
        client.post(
            f"/api/jobs/{job_id}/resumes",
            files=[("files", ("手动上传.docx", make_pdf_docx(), DOCX_SUBTYPE))],
        )
        result = client.post(
            "/api/pool/assign",
            json={"resume_ids": [entry["id"]], "job_id": job_id, "evaluate": True},
        )
        assert result.json()["assigned"] == 1

        # 默认 pool 范围仍只含邮箱简历
        assert {item["id"] for item in pool_items(client)} == {entry["id"]}

        all_items = client.get("/api/pool/resumes", params={"scope": "all"}).json()
        assert len(all_items) == 2
        email_item = next(item for item in all_items if item["id"] == entry["id"])
        assert email_item["evaluation"]["overall_score"] == 86
        assert email_item["evaluation"]["gender"] == "female"
        assert email_item["evaluation"]["age"] == 28
        assert email_item["evaluated_jobs"] == [{"job_id": job_id, "job_name": "资深数据架构师"}]
        upload_item = next(item for item in all_items if item["id"] != entry["id"])
        assert upload_item["job_id"] == job_id
        assert upload_item["source"] == "upload"
        assert upload_item["evaluation"] is None
        assert upload_item["evaluated_jobs"] == []


def test_migration_0006_allows_null_job_id(tmp_path, monkeypatch) -> None:
    """迁移 0006 后 resumes.job_id 允许为空，可表达简历池简历。"""

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
        "VALUES ('r1', 'job-1', '旧简历.pdf', 'application/pdf', '/tmp/x.pdf', 'abc', 1, '2026-01-01', '2026-01-01')"
    )
    connection.commit()
    connection.close()

    command.stamp(config, "20260902_0005")
    command.upgrade(config, "20260903_0007")

    connection = sqlite3.connect(tmp_path / "mig.db")
    connection.execute(
        "INSERT INTO resumes (id, job_id, original_name, mime_type, storage_path, sha256, size_bytes, created_at, updated_at) "
        "VALUES ('r2', NULL, '池简历.pdf', 'application/pdf', '/tmp/y.pdf', 'def', 1, '2026-01-01', '2026-01-01')"
    )
    connection.commit()
    assert connection.execute("SELECT job_id FROM resumes WHERE id = 'r2'").fetchone()[0] is None
    connection.close()
