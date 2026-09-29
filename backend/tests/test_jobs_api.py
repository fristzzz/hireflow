"""通过公开 HTTP 接口验证岗位工作流。"""

from __future__ import annotations

from fastapi.testclient import TestClient
from docx import Document
from io import BytesIO
import pymupdf as fitz

from app.documents import DocumentProcessor
from app.main import create_app


def test_create_job_and_read_it_back(tmp_path) -> None:
    app = create_app(database_url=f"sqlite:///{tmp_path / 'test.db'}")

    with TestClient(app) as client:
        response = client.post(
            "/api/jobs",
            json={
                "name": "高级后端工程师",
                "jd_text": "负责 Python 平台研发，要求五年以上经验。",
                "prompt": "重点考察工程能力与复杂系统经验。",
            },
        )

        assert response.status_code == 201
        job_id = response.json()["id"]
        fetched = client.get(f"/api/jobs/{job_id}")
        assert fetched.status_code == 200
        assert fetched.json()["name"] == "高级后端工程师"
        assert fetched.json()["resume_count"] == 0
        assert fetched.json()["scoring_source"] == "default"
        assert len(fetched.json()["scoring_dimensions"]) == 6
        assert sum(item["weight"] for item in fetched.json()["scoring_dimensions"]) == 100


def test_create_job_returns_table_scoring_dimensions(tmp_path) -> None:
    app = create_app(database_url=f"sqlite:///{tmp_path / 'test.db'}")
    prompt = """
| 评分项 | 权重 |
| --- | --- |
| CDC 同步引擎能力 | 30分 |
| 数据建模与分层 | 25 |
| 实时架构设计 | 20 |
| 领域加分项 | 15 |
| 工程落地 | 10 |
"""

    with TestClient(app) as client:
        created = client.post(
            "/api/jobs",
            json={"name": "资深数据架构师", "jd_text": "实时数据引擎与 CDC。", "prompt": prompt},
        )
        assert created.status_code == 201
        body = created.json()
        assert body["scoring_source"] == "prompt"
        assert [item["name"] for item in body["scoring_dimensions"]] == [
            "CDC 同步引擎能力",
            "数据建模与分层",
            "实时架构设计",
            "领域加分项",
            "工程落地",
        ]

        preview = client.post("/api/scoring-preview", json={"prompt": prompt})
        assert preview.status_code == 200
        assert preview.json()["source"] == "prompt"
        assert preview.json()["dimensions"][0]["weight"] == 30
        assert preview.json()["dimensions"][3]["kind"] == "bonus"


def test_create_and_update_job_scoring_profile_kinds(tmp_path) -> None:
    app = create_app(database_url=f"sqlite:///{tmp_path / 'test.db'}")
    prompt = """
### 1. CDC 同步引擎能力：30分
### 2. 数据建模与分层：25分
### 3. 实时架构设计：20分
### 4. 领域加分项：15分
### 5. 工程落地：10分
"""
    profile = [
        {"name": "CDC 同步引擎能力", "weight": 30, "kind": "knockout"},
        {"name": "数据建模与分层", "weight": 25, "kind": "core"},
        {"name": "实时架构设计", "weight": 20, "kind": "core"},
        {"name": "领域加分项", "weight": 15, "kind": "bonus"},
        {"name": "工程落地", "weight": 10, "kind": "core"},
    ]

    with TestClient(app) as client:
        created = client.post(
            "/api/jobs",
            json={"name": "资深数据架构师", "jd_text": "实时数据引擎。", "prompt": prompt, "scoring_profile": profile},
        )
        assert created.status_code == 201
        body = created.json()
        assert body["scoring_source"] == "profile"
        assert body["scoring_dimensions"][0]["kind"] == "knockout"
        assert body["scoring_dimensions"][3]["kind"] == "bonus"

        profile[0]["kind"] = "core"
        profile[1]["kind"] = "knockout"
        updated = client.patch(f"/api/jobs/{body['id']}", json={"scoring_profile": profile})
        assert updated.status_code == 200
        assert updated.json()["scoring_dimensions"][0]["kind"] == "core"
        assert updated.json()["scoring_dimensions"][1]["kind"] == "knockout"


def test_scoring_preview_rejects_unparsed_rubric(tmp_path) -> None:
    app = create_app(database_url=f"sqlite:///{tmp_path / 'test.db'}")

    with TestClient(app) as client:
        preview = client.post(
            "/api/scoring-preview",
            json={"prompt": "评分标准：CDC 30分，建模 70分"},
        )
        assert preview.status_code == 400
        assert "无法识别" in preview.json()["detail"]


def test_upload_docx_and_reject_duplicate(tmp_path) -> None:
    app = create_app(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        data_dir=tmp_path / "data",
    )

    document = Document()
    document.add_heading("张晨", level=1)
    document.add_paragraph("五年 Python 后端开发经验")
    buffer = BytesIO()
    document.save(buffer)
    content = buffer.getvalue()

    with TestClient(app) as client:
        job = client.post(
            "/api/jobs",
            json={"name": "后端工程师", "jd_text": "Python", "prompt": "关注工程经验"},
        ).json()

        uploaded = client.post(
            f"/api/jobs/{job['id']}/resumes",
            files=[("files", ("张晨.docx", content, "application/vnd.openxmlformats-officedocument.wordprocessingml.document"))],
        )
        assert uploaded.status_code == 201
        assert uploaded.json()["uploaded"][0]["parse_status"] == "ready"
        assert uploaded.json()["uploaded"][0]["page_count"] is None
        resume_id = uploaded.json()["uploaded"][0]["id"]

        text = client.get(f"/api/resumes/{resume_id}/text")
        assert text.status_code == 200
        assert text.headers["content-type"].startswith("text/plain")
        assert "五年 Python 后端开发经验" in text.text

        downloaded = client.get(f"/api/resumes/{resume_id}/text?download=true")
        assert downloaded.status_code == 200
        assert "attachment" in downloaded.headers["content-disposition"]

        duplicate = client.post(
            f"/api/jobs/{job['id']}/resumes",
            files=[("files", ("再次上传.docx", content, "application/vnd.openxmlformats-officedocument.wordprocessingml.document"))],
        )
        assert duplicate.status_code == 201
        assert duplicate.json()["uploaded"] == []
        assert "重复" in duplicate.json()["rejected"][0]["reason"]


def test_upload_pdf_parses_text_into_database_without_screening(tmp_path, monkeypatch) -> None:
    async def fake_extract_text(self: DocumentProcessor, storage_path: str) -> str:
        assert storage_path.endswith(".pdf")
        return "[第 1 页]\n候选人具有分布式系统经验"

    monkeypatch.setattr(DocumentProcessor, "extract_text", fake_extract_text)
    app = create_app(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        data_dir=tmp_path / "data",
    )
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "Resume")
    content = document.tobytes()
    document.close()

    with TestClient(app) as client:
        job = client.post(
            "/api/jobs",
            json={"name": "平台工程师", "jd_text": "分布式系统", "prompt": "关注系统经验"},
        ).json()
        uploaded = client.post(
            f"/api/jobs/{job['id']}/resumes",
            files=[("files", ("候选人.pdf", content, "application/pdf"))],
        )
        assert uploaded.status_code == 201
        resume_id = uploaded.json()["uploaded"][0]["id"]

        resumes = client.get(f"/api/jobs/{job['id']}/resumes").json()
        assert resumes[0]["parse_status"] == "ready"
        assert client.get(f"/api/resumes/{resume_id}/text").text == "[第 1 页]\n候选人具有分布式系统经验"

        preview = client.get(f"/api/resumes/{resume_id}/preview")
        assert preview.status_code == 200
        assert preview.headers["content-type"].startswith("text/html")
        assert f"/api/resumes/{resume_id}/preview/pages/1" in preview.text

        preview_page = client.get(f"/api/resumes/{resume_id}/preview/pages/1")
        assert preview_page.status_code == 200
        assert preview_page.headers["content-type"] == "image/png"
        assert preview_page.content.startswith(b"\x89PNG")


def test_upload_text_pdf_prefers_embedded_text_over_ocr(tmp_path, monkeypatch) -> None:
    """文本型 PDF 应保留原生姓名，不能被低质量 OCR 覆盖。"""

    monkeypatch.setattr(
        DocumentProcessor,
        "_extract_page_with_ocr",
        staticmethod(lambda _image_bytes: "SB"),
    )
    app = create_app(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        data_dir=tmp_path / "data",
    )
    app.state.settings.vision_enabled = False

    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "Wu Yang")
    page.insert_text((72, 96), "11 years data engineering experience")
    content = document.tobytes()
    document.close()

    with TestClient(app) as client:
        job = client.post(
            "/api/jobs",
            json={"name": "数据工程师", "jd_text": "数据平台", "prompt": "关注工程经验"},
        ).json()
        uploaded = client.post(
            f"/api/jobs/{job['id']}/resumes",
            files=[("files", ("Wu Yang.pdf", content, "application/pdf"))],
        )
        assert uploaded.status_code == 201
        resume_id = uploaded.json()["uploaded"][0]["id"]

        parsed_text = client.get(f"/api/resumes/{resume_id}/text")
        assert parsed_text.status_code == 200
        assert "Wu Yang" in parsed_text.text
        assert "SB" not in parsed_text.text


def test_upload_scanned_pdf_falls_back_to_ocr(tmp_path, monkeypatch) -> None:
    """无可用文本层的扫描 PDF 仍应通过 OCR 入库。"""

    monkeypatch.setattr(
        DocumentProcessor,
        "_extract_page_with_ocr",
        staticmethod(lambda _image_bytes: "扫描候选人\n十年数据平台经验"),
    )
    app = create_app(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        data_dir=tmp_path / "data",
    )
    app.state.settings.vision_enabled = False

    document = fitz.open()
    document.new_page()
    content = document.tobytes()
    document.close()

    with TestClient(app) as client:
        job = client.post(
            "/api/jobs",
            json={"name": "数据工程师", "jd_text": "数据平台", "prompt": "关注工程经验"},
        ).json()
        uploaded = client.post(
            f"/api/jobs/{job['id']}/resumes",
            files=[("files", ("扫描简历.pdf", content, "application/pdf"))],
        )
        assert uploaded.status_code == 201
        resume_id = uploaded.json()["uploaded"][0]["id"]

        parsed_text = client.get(f"/api/resumes/{resume_id}/text")
        assert parsed_text.status_code == 200
        assert "扫描候选人" in parsed_text.text


def test_upload_boss_filename_uses_candidate_name_as_display(tmp_path) -> None:
    app = create_app(database_url=f"sqlite:///{tmp_path / 'test.db'}", data_dir=tmp_path / "data")
    document = Document()
    document.add_paragraph("五年 Python 后端开发经验")
    buffer = BytesIO()
    document.save(buffer)

    with TestClient(app) as client:
        job = client.post(
            "/api/jobs",
            json={"name": "后端工程师", "jd_text": "Python", "prompt": "工程能力"},
        ).json()
        uploaded = client.post(
            f"/api/jobs/{job['id']}/resumes",
            files=[
                (
                    "files",
                    (
                        "【测试工程师_深圳_28-50K】江贺琨_15年.docx",
                        buffer.getvalue(),
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    ),
                )
            ],
        ).json()["uploaded"][0]
        assert uploaded["original_name"].endswith("江贺琨_15年.docx")
        assert uploaded["display_name"] == "江贺琨"


def test_upload_garbled_ocr_scan_is_parse_failure(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        DocumentProcessor,
        "_extract_page_with_ocr",
        staticmethod(lambda _image_bytes: "ile 619996 BB 深说信食"),
    )
    app = create_app(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        data_dir=tmp_path / "data",
    )
    app.state.settings.vision_enabled = False
    document = fitz.open()
    document.new_page()
    content = document.tobytes()
    document.close()

    with TestClient(app) as client:
        job = client.post(
            "/api/jobs",
            json={"name": "数据工程师", "jd_text": "数据平台", "prompt": "关注工程经验"},
        ).json()
        uploaded = client.post(
            f"/api/jobs/{job['id']}/resumes",
            files=[("files", ("扫描简历.pdf", content, "application/pdf"))],
        )
        resume_id = uploaded.json()["uploaded"][0]["id"]
        resume = None
        for _ in range(50):
            resume = client.get(f"/api/jobs/{job['id']}/resumes").json()[0]
            if resume["id"] == resume_id and resume["parse_status"] != "parsing":
                break
        assert resume is not None
        assert resume["parse_status"] == "failed"
        assert resume["parse_error"]
        assert "解析失败" not in resume["parse_error"] or "OCR" in resume["parse_error"] or "扫描" in resume["parse_error"]
        assert "评估" not in resume["parse_error"]


def test_upload_pdf_with_trailing_blank_page_succeeds(tmp_path) -> None:
    app = create_app(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        data_dir=tmp_path / "data",
    )
    app.state.settings.vision_enabled = False
    document = fitz.open()
    page1 = document.new_page()
    page1.insert_text((50, 50), "Resume of John Doe. Experience: Senior Java Engineer at Acme Corp. Education: BS in Computer Science.")
    document.new_page()  # Blank trailing page
    content = document.tobytes()
    document.close()

    with TestClient(app) as client:
        job = client.post(
            "/api/jobs",
            json={"name": "数据工程师", "jd_text": "数据平台", "prompt": "关注工程经验"},
        ).json()
        uploaded = client.post(
            f"/api/jobs/{job['id']}/resumes",
            files=[("files", ("带空白页简历.pdf", content, "application/pdf"))],
        )
        resume_id = uploaded.json()["uploaded"][0]["id"]
        resume = None
        for _ in range(50):
            resume = client.get(f"/api/jobs/{job['id']}/resumes").json()[0]
            if resume["id"] == resume_id and resume["parse_status"] != "parsing":
                break
        assert resume is not None
        assert resume["parse_status"] == "ready"
        assert resume["parse_error"] is None


def test_upload_pdf_with_watermark_hash_layer_falls_back_to_ocr(tmp_path, monkeypatch) -> None:
    """带平台防伪水印散列码的图片型 PDF 不应误认文本层，应正常降级 OCR 识别。"""

    monkeypatch.setattr(
        DocumentProcessor,
        "_extract_page_with_ocr",
        staticmethod(lambda _image_bytes: "测试工程师\n十年分布式系统测试经验\n项目经历：自动化平台搭建"),
    )
    app = create_app(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        data_dir=tmp_path / "data",
    )
    app.state.settings.vision_enabled = False

    document = fitz.open()
    page = document.new_page()
    # 模拟 BOSS 直聘等平台的防伪水印 hash 字符串
    page.insert_text((72, 72), "2b4f5288689a6aeb1Hx93921EVtXx4q_VPOYWOKimffZMxJj2A~~")
    content = document.tobytes()
    document.close()

    with TestClient(app) as client:
        job = client.post(
            "/api/jobs",
            json={"name": "测试工程师", "jd_text": "自动化测试", "prompt": "关注测试架构"},
        ).json()
        uploaded = client.post(
            f"/api/jobs/{job['id']}/resumes",
            files=[("files", ("防伪水印简历.pdf", content, "application/pdf"))],
        )
        assert uploaded.status_code == 201
        resume_id = uploaded.json()["uploaded"][0]["id"]
        resume = None
        for _ in range(50):
            resume = client.get(f"/api/jobs/{job['id']}/resumes").json()[0]
            if resume["id"] == resume_id and resume["parse_status"] != "parsing":
                break
        assert resume is not None
        assert resume["parse_status"] == "ready"
        assert resume["parse_error"] is None
        parsed_text = client.get(f"/api/resumes/{resume_id}/text")
        assert parsed_text.status_code == 200
        assert "分布式系统测试经验" in parsed_text.text


