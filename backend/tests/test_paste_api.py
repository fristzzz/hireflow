"""验证粘贴文本录入简历池：创建、空文本拒绝、身份查重、确认录入与迁移 0009。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.models import Resume


def make_app(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        data_dir=tmp_path / "data",
        imap_poll_seconds=0,
    )
    return create_app(settings=settings)


def pool_items(client: TestClient) -> list[dict]:
    return client.get("/api/pool/resumes").json()


def test_paste_creates_pool_resume(tmp_path) -> None:
    """粘贴文本直接就绪入池：落盘 .txt、规范化文本入库、身份待提取。"""

    app = make_app(tmp_path)
    with TestClient(app) as client:
        result = client.post(
            "/api/pool/resumes/paste",
            json={"text": "  张三\r\n五年数据平台经验\r\n\r\n\r\n邮箱 zs@example.com  ", "name": "张三"},
        )
        assert result.status_code == 200
        body = result.json()
        assert body["created"] is True
        assert body["duplicates"] == []
        resume = body["resume"]
        assert resume["source"] == "paste"
        assert resume["mime_type"] == "text/plain"
        assert resume["parse_status"] == "ready"
        assert resume["job_id"] is None
        assert resume["display_name"] == "张三"

        # 规范化文本可通过文本接口取回（预览/下载链路零改动）
        text_response = client.get(f"/api/resumes/{resume['id']}/text")
        assert text_response.status_code == 200
        assert text_response.text == "张三\n五年数据平台经验\n\n邮箱 zs@example.com"

        # 出现在简历池默认列表中
        assert [item["id"] for item in pool_items(client)] == [resume["id"]]

    with app.state.session_factory() as session:
        row = session.get(Resume, resume["id"])
        assert row.identity_status == "pending"
        assert row.parsed_text_source == "extracted"
        storage = Path(row.storage_path)
        assert storage.is_file() and storage.suffix == ".txt"
        assert storage.read_text(encoding="utf-8") == row.parsed_text


def test_paste_rejects_empty_text(tmp_path) -> None:
    app = make_app(tmp_path)
    with TestClient(app) as client:
        result = client.post("/api/pool/resumes/paste", json={"text": " \r\n\n  "})
        assert result.status_code == 400
        assert pool_items(client) == []


def test_paste_warns_on_duplicate_identity(tmp_path) -> None:
    """未 confirm 时按邮箱命中疑似重复：返回候选列表且不创建。"""

    app = make_app(tmp_path)
    with TestClient(app) as client:
        first = client.post(
            "/api/pool/resumes/paste",
            json={"text": "旧简历，邮箱 dup@example.com", "confirm": True},
        )
        assert first.json()["created"] is True

        result = client.post(
            "/api/pool/resumes/paste",
            json={"text": "另一份文本，同样写着 dup@example.com"},
        )
        body = result.json()
        assert body["created"] is False
        assert body["resume"] is None
        assert len(body["duplicates"]) == 1
        assert body["duplicates"][0]["id"] == first.json()["resume"]["id"]
        assert "邮箱" in body["duplicates"][0]["matched_fields"]
        assert len(pool_items(client)) == 1


def test_paste_warns_on_duplicate_name(tmp_path) -> None:
    """用户填写的姓名命中池内已有简历文本时给出疑似重复提醒。"""

    app = make_app(tmp_path)
    with TestClient(app) as client:
        client.post("/api/pool/resumes/paste", json={"text": "候选人李雷的简历", "confirm": True})
        result = client.post("/api/pool/resumes/paste", json={"text": "完全不同的经历文本", "name": "李雷"})
        body = result.json()
        assert body["created"] is False
        assert "姓名" in body["duplicates"][0]["matched_fields"]
        assert len(pool_items(client)) == 1


def test_paste_confirm_creates_despite_duplicate(tmp_path) -> None:
    app = make_app(tmp_path)
    with TestClient(app) as client:
        client.post("/api/pool/resumes/paste", json={"text": "邮箱 dup@example.com", "confirm": True})
        result = client.post(
            "/api/pool/resumes/paste",
            json={"text": "再次粘贴 dup@example.com", "confirm": True},
        )
        assert result.json()["created"] is True
        assert len(pool_items(client)) == 2


def test_paste_without_identity_signals_creates_directly(tmp_path) -> None:
    """无邮箱/手机号且未填姓名时无从查重，直接录入。"""

    app = make_app(tmp_path)
    with TestClient(app) as client:
        client.post("/api/pool/resumes/paste", json={"text": "第一份纯经历文本", "confirm": True})
        result = client.post("/api/pool/resumes/paste", json={"text": "第二份纯经历文本"})
        assert result.json()["created"] is True
        assert len(pool_items(client)) == 2


def test_migration_0009_adds_identity_columns_and_backfills(tmp_path, monkeypatch) -> None:
    """迁移 0009 加四列，存量池简历回填 pending，岗位简历为 none。"""

    from alembic import command
    from alembic.config import Config

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'mig.db'}")
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parents[1] / "alembic"))

    connection = sqlite3.connect(tmp_path / "mig.db")
    connection.execute(
        "CREATE TABLE resumes ("
        "id VARCHAR(36) PRIMARY KEY, job_id VARCHAR(36), deleted_at DATETIME, "
        "original_name VARCHAR(255) NOT NULL, mime_type VARCHAR(120) NOT NULL, "
        "storage_path TEXT NOT NULL, sha256 VARCHAR(64) NOT NULL, "
        "size_bytes INTEGER NOT NULL, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL)"
    )
    connection.execute(
        "INSERT INTO resumes (id, job_id, deleted_at, original_name, mime_type, storage_path, sha256, size_bytes, created_at, updated_at) "
        "VALUES ('pool-1', NULL, NULL, '池简历.pdf', 'application/pdf', '/tmp/x.pdf', 'abc', 1, '2026-01-01', '2026-01-01')"
    )
    connection.execute(
        "INSERT INTO resumes (id, job_id, deleted_at, original_name, mime_type, storage_path, sha256, size_bytes, created_at, updated_at) "
        "VALUES ('job-1', 'j1', NULL, '岗位简历.pdf', 'application/pdf', '/tmp/y.pdf', 'def', 1, '2026-01-01', '2026-01-01')"
    )
    connection.commit()
    connection.close()

    command.stamp(config, "20260908_0008")
    command.upgrade(config, "20260915_0009")

    connection = sqlite3.connect(tmp_path / "mig.db")
    columns = {row[1] for row in connection.execute("PRAGMA table_info(resumes)")}
    assert {"identity_name", "identity_email", "identity_phone", "identity_status"} <= columns
    assert connection.execute("SELECT identity_status FROM resumes WHERE id = 'pool-1'").fetchone()[0] == "pending"
    assert connection.execute("SELECT identity_status FROM resumes WHERE id = 'job-1'").fetchone()[0] == "none"
    connection.close()
