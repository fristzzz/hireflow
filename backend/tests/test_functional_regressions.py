"""覆盖跨岗位分配、回收站和中断恢复的功能边界。"""
from __future__ import annotations

from io import BytesIO
from pathlib import Path
import time

from docx import Document
from auth_helpers import authed_client
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.models import Resume
from test_pool_api import FakeEvaluator


def build_app(tmp_path):
    return create_app(settings=Settings(_env_file=None, database_url=f"sqlite:///{tmp_path}/test.db", data_dir=tmp_path / "data"), evaluator=FakeEvaluator())


def job(client, name):
    return client.post("/api/jobs", json={"name": name, "jd_text": "合成测试职责", "prompt": "合成测试标准"}).json()["id"]


def upload(client, job_id):
    document = Document()
    document.add_paragraph("功能回归合成材料，不含个人信息。")
    data = BytesIO()
    document.save(data)
    return client.post(f"/api/jobs/{job_id}/resumes", files=[("files", ("synthetic.docx", data.getvalue(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document"))]).json()["uploaded"][0]["id"]


def assign(client, resume_id, job_id, evaluate=False):
    return client.post("/api/pool/assign", json={"resume_ids": [resume_id], "job_id": job_id, "evaluate": evaluate})


def wait(client, run_id):
    for _ in range(100):
        row = client.get(f"/api/runs/{run_id}").json()
        if row["status"] not in {"queued", "running"}:
            assert row["status"] == "completed"
            return
        time.sleep(0.01)
    raise AssertionError("合成评估未完成")


def test_purging_old_job_preserves_transferred_files(tmp_path):
    app = build_app(tmp_path)
    with authed_client(app) as client:
        old, new = job(client, "旧岗位"), job(client, "新岗位")
        resume_id = upload(client, old)
        assert assign(client, resume_id, new).status_code == 200
        client.delete(f"/api/jobs/{old}")
        assert client.delete(f"/api/trash/job/{old}").status_code == 204
        assert client.get(f"/api/resumes/{resume_id}/original").status_code == 200
        with app.state.session_factory() as session:
            path = Path(session.get(Resume, resume_id).storage_path)
        client.delete(f"/api/jobs/{new}")
        client.delete(f"/api/trash/job/{new}")
        assert not path.exists()


def test_pool_resume_trash_restore_and_purge(tmp_path):
    app = build_app(tmp_path)
    with authed_client(app) as client:
        resume_id = upload(client, job(client, "测试岗位"))
        with app.state.session_factory() as session:
            session.get(Resume, resume_id).job_id = None
            session.commit()
        assert client.delete(f"/api/resumes/{resume_id}").status_code == 204
        assert client.get("/api/trash").json()["total"] == 1
        assert client.post(f"/api/trash/resume/{resume_id}/restore").status_code == 200
        client.delete(f"/api/resumes/{resume_id}")
        assert client.delete("/api/trash").status_code == 200


def test_assignment_handles_duplicate_in_trash(tmp_path):
    app = build_app(tmp_path)
    with authed_client(app) as client:
        old, new = job(client, "旧岗位"), job(client, "新岗位")
        resume_id = upload(client, old)
        raw = client.get(f"/api/resumes/{resume_id}/original").content
        duplicate = client.post(f"/api/jobs/{new}/resumes", files=[("files", ("copy.docx", raw, "application/vnd.openxmlformats-officedocument.wordprocessingml.document"))]).json()["uploaded"][0]["id"]
        client.delete(f"/api/resumes/{duplicate}")
        response = assign(client, resume_id, new)
        assert response.status_code == 200
        assert response.json()["assigned"] == 0
        assert "回收站" in response.json()["skipped"][0]["reason"]


def test_assign_then_evaluate_retains_each_jobs_results(tmp_path):
    app = build_app(tmp_path)
    with authed_client(app) as client:
        old, new = job(client, "旧岗位"), job(client, "新岗位")
        resume_id = upload(client, old)
        first = assign(client, resume_id, old, True).json()["run"]
        assert first is not None
        wait(client, first["id"])
        assert assign(client, resume_id, new).json()["assigned"] == 1
        second = assign(client, resume_id, new, True).json()["run"]
        assert second is not None
        wait(client, second["id"])
        for job_id in (old, new):
            page = client.get(f"/api/jobs/{job_id}/evaluations").json()
            assert page["total"] == 1
            assert len(client.get(f"/api/jobs/{job_id}/export?format=json").json()["items"]) == 1
        old_result = client.get(f"/api/jobs/{old}/evaluations").json()["items"][0]
        rescored = client.post("/api/evaluations/rescore", json={"evaluation_ids": [old_result["id"]]})
        assert rescored.status_code == 201
        wait(client, rescored.json()["id"])


def test_recovery_counts_and_pool_pending_status(tmp_path):
    app = build_app(tmp_path)
    with authed_client(app) as client:
        app.state.screening_runner.start = lambda _: None
        job_id = job(client, "测试岗位")
        resume_id = upload(client, job_id)
        client.post(f"/api/jobs/{job_id}/runs", json={"resume_ids": [resume_id]})
        item = client.get("/api/pool/resumes?scope=all").json()[0]
        assert item["evaluation"] is None
        assert item["evaluation_status"] == "queued"
        app.state.screening_runner.recover_interrupted()
        run = client.get(f"/api/jobs/{job_id}/runs").json()["items"][0]
        assert run["failed_count"] == 1
        assert client.get("/api/pool/resumes?scope=all").json()[0]["evaluation_status"] == "failed"


def test_migration_backfills_job_results_and_enforces_uniqueness(tmp_path, monkeypatch):
    """旧结构升级补齐岗位归属，并恢复每个岗位的当前评分。"""
    import sqlite3
    from alembic import command
    from alembic.config import Config

    app = build_app(tmp_path)
    with authed_client(app) as client:
        old, new = job(client, "旧岗位"), job(client, "新岗位")
        resume_id = upload(client, old)
        wait(client, assign(client, resume_id, old, True).json()["run"]["id"])
        wait(client, assign(client, resume_id, new, True).json()["run"]["id"])
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parents[1] / "alembic"))
    command.stamp(config, "head")
    command.downgrade(config, "20260903_0007")
    connection = sqlite3.connect(tmp_path / "test.db")
    assert connection.execute("SELECT COUNT(*) FROM evaluations WHERE is_current = 1").fetchone()[0] == 1
    connection.close()
    command.upgrade(config, "head")
    with authed_client(build_app(tmp_path)) as client:
        assert client.get(f"/api/jobs/{old}/evaluations").json()["total"] == 1
        assert client.get(f"/api/jobs/{new}/evaluations").json()["total"] == 1
    connection = sqlite3.connect(tmp_path / "test.db")
    assert connection.execute("SELECT COUNT(*) FROM evaluations WHERE job_id IS NOT NULL AND is_current = 1").fetchone()[0] == 2
    indexes = {row[1] for row in connection.execute("PRAGMA index_list(evaluations)")}
    assert "uq_evaluation_current_resume_job" in indexes
    assert "uq_evaluation_current_resume" not in indexes
    connection.close()


def test_fresh_database_upgrades_to_head(tmp_path, monkeypatch):
    """全新安装应能按完整迁移链创建表。"""
    from alembic import command
    from alembic.config import Config
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/fresh.db")
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parents[1] / "alembic"))
    command.upgrade(config, "head")
