"""验证岗位内粘贴简历：创建并自动启动评分、岗位内查重、上限与 404。"""

from __future__ import annotations

from pathlib import Path

from auth_helpers import authed_client
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.models import Resume
from app.schemas import EvaluationCard


class FakeEvaluator:
    async def evaluate(self, *, jd_text: str, prompt: str, resume_text: str, scoring_profile=None) -> EvaluationCard:
        return EvaluationCard.model_validate(
            {
                "candidate": {"name": "候选人"},
                "overall_score": 80,
                "match_level": "medium",
                "confidence": 0.8,
                "summary": "经历基本匹配。",
                "criteria": [{"criterion": "综合能力", "score": 80, "status": "met", "evidence": [{"text": "五年经验", "location": "段落 1"}]}],
            }
        )


def make_app(tmp_path, **overrides):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        data_dir=tmp_path / "data",
        imap_poll_seconds=0,
        **overrides,
    )
    return create_app(settings=settings, evaluator=FakeEvaluator())


def create_job(client: TestClient, name: str = "后端工程师") -> str:
    created = client.post(
        "/api/jobs",
        json={"name": name, "jd_text": "负责后端服务", "prompt": "考察后端工程能力"},
    )
    assert created.status_code == 201
    return created.json()["id"]


def job_resumes(client: TestClient, job_id: str) -> list[dict]:
    return client.get(f"/api/jobs/{job_id}/resumes").json()


def test_paste_creates_resume_and_starts_run(tmp_path) -> None:
    """粘贴创建成功：简历入库就绪、identity_status 为 none，并立即启动只含该简历的评分任务。"""

    app = make_app(tmp_path)
    with authed_client(app) as client:
        job_id = create_job(client)
        result = client.post(
            f"/api/jobs/{job_id}/resumes/paste",
            json={"text": "  张三\r\n五年后端经验\r\n\r\n\r\n邮箱 zs@example.com  ", "name": "张三"},
        )
        assert result.status_code == 200
        body = result.json()
        assert body["created"] is True
        assert body["duplicates"] == []
        resume = body["resume"]
        assert resume["job_id"] == job_id
        assert resume["source"] == "paste"
        assert resume["mime_type"] == "text/plain"
        assert resume["parse_status"] == "ready"
        assert resume["display_name"] == "张三"
        run = body["run"]
        assert run is not None
        assert run["job_id"] == job_id
        assert run["total_count"] == 1

        # 文本接口返回规范化后的内容
        text_response = client.get(f"/api/resumes/{resume['id']}/text")
        assert text_response.text == "张三\n五年后端经验\n\n邮箱 zs@example.com"

        # 评分任务确实在任务中心可见且只含这一份简历
        runs = client.get(f"/api/jobs/{job_id}/runs").json()
        assert runs["total"] == 1
        evaluations = client.get(f"/api/runs/{run['id']}/evaluations").json()
        assert [item["resume_id"] for item in evaluations] == [resume["id"]]

    with app.state.session_factory() as session:
        row = session.get(Resume, resume["id"])
        # 岗位直传不做身份提取
        assert row.identity_status == "none"
        storage = Path(row.storage_path)
        assert storage.is_file() and storage.suffix == ".txt"
        assert storage.parent.name == job_id


def test_paste_rejects_empty_text(tmp_path) -> None:
    app = make_app(tmp_path)
    with authed_client(app) as client:
        job_id = create_job(client)
        result = client.post(f"/api/jobs/{job_id}/resumes/paste", json={"text": " \r\n\n  "})
        assert result.status_code == 400
        assert job_resumes(client, job_id) == []


def test_paste_unknown_job_returns_404(tmp_path) -> None:
    app = make_app(tmp_path)
    with authed_client(app) as client:
        result = client.post("/api/jobs/不存在/resumes/paste", json={"text": "任意简历文本"})
        assert result.status_code == 404


def test_paste_rejects_when_job_is_full(tmp_path) -> None:
    app = make_app(tmp_path, max_resumes_per_job=1)
    with authed_client(app) as client:
        job_id = create_job(client)
        first = client.post(f"/api/jobs/{job_id}/resumes/paste", json={"text": "第一份简历", "confirm": True})
        assert first.json()["created"] is True
        result = client.post(f"/api/jobs/{job_id}/resumes/paste", json={"text": "第二份简历"})
        assert result.status_code == 400
        assert "上限" in result.json()["detail"]
        assert len(job_resumes(client, job_id)) == 1


def test_paste_warns_on_duplicate_identity_within_job(tmp_path) -> None:
    """同岗位内邮箱命中疑似重复：不创建也不启动评分；confirm 后才录入。"""

    app = make_app(tmp_path)
    with authed_client(app) as client:
        job_id = create_job(client)
        client.post(f"/api/jobs/{job_id}/resumes/paste", json={"text": "旧简历，邮箱 dup@example.com", "confirm": True})

        result = client.post(f"/api/jobs/{job_id}/resumes/paste", json={"text": "另一段文本，同样写着 dup@example.com"})
        body = result.json()
        assert body["created"] is False
        assert body["run"] is None
        assert len(body["duplicates"]) == 1
        assert "邮箱" in body["duplicates"][0]["matched_fields"]
        assert len(job_resumes(client, job_id)) == 1
        assert client.get(f"/api/jobs/{job_id}/runs").json()["total"] == 1

        confirmed = client.post(
            f"/api/jobs/{job_id}/resumes/paste",
            json={"text": "另一段文本，同样写着 dup@example.com", "confirm": True},
        )
        assert confirmed.json()["created"] is True
        assert confirmed.json()["run"]["total_count"] == 1
        assert len(job_resumes(client, job_id)) == 2


def test_paste_duplicate_scope_is_per_job(tmp_path) -> None:
    """身份查重只限本岗位：另一个岗位里的相同邮箱不构成疑似重复。"""

    app = make_app(tmp_path)
    with authed_client(app) as client:
        job_a = create_job(client, "岗位 A")
        job_b = create_job(client, "岗位 B")
        client.post(f"/api/jobs/{job_a}/resumes/paste", json={"text": "邮箱 same@example.com", "confirm": True})
        result = client.post(f"/api/jobs/{job_b}/resumes/paste", json={"text": "不同文本 same@example.com"})
        assert result.json()["created"] is True
        assert len(job_resumes(client, job_b)) == 1


def test_paste_same_content_conflict_even_with_confirm(tmp_path) -> None:
    """内容完全相同（sha256 一致）时 confirm 也不能绕过，避免违反岗位内唯一约束。"""

    app = make_app(tmp_path)
    with authed_client(app) as client:
        job_id = create_job(client)
        client.post(f"/api/jobs/{job_id}/resumes/paste", json={"text": "完全相同的简历文本"})
        result = client.post(f"/api/jobs/{job_id}/resumes/paste", json={"text": "完全相同的简历文本", "confirm": True})
        assert result.status_code == 409
        assert len(job_resumes(client, job_id)) == 1
