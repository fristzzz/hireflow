"""验证筛选结果 Word 导出的可读层次与字体。"""

from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
from types import SimpleNamespace

from docx import Document
from docx.oxml.ns import qn

from app.exporter import build_docx


def make_evaluation() -> SimpleNamespace:
    return SimpleNamespace(
        resume=SimpleNamespace(original_name="张晨.pdf"),
        run=SimpleNamespace(created_at=datetime(2026, 8, 10, 8, 0, tzinfo=timezone.utc)),
        review_status="advance",
        review_note="进入技术面",
        status="completed",
        error=None,
        result_json={
            "candidate": {"name": "张晨", "current_role": "后端工程师"},
            "overall_score": 86,
            "match_level": "high",
            "confidence": 0.9,
            "background_bonus": 2,
            "scoring_version": "2",
            "summary": "工程经历与岗位高度匹配。",
            "criteria": [
                {
                    "criterion": "CDC 与实时计算",
                    "score": 40,
                    "weight": 30,
                    "weighted_score": 12,
                    "status": "not_met",
                    "kind": "knockout",
                    "knockout": True,
                    "evidence": [{"text": "仅使用过同步产品", "location": "第 1 页"}],
                },
                {
                    "criterion": "加分项与场景契合",
                    "score": 80,
                    "weight": 15,
                    "weighted_score": 12,
                    "status": "met",
                    "kind": "bonus",
                    "knockout": False,
                    "evidence": [],
                },
            ],
            "strengths": ["Java 经验扎实"],
            "gaps": ["缺少引擎开发"],
            "risk_flags": ["需核实个人贡献"],
            "interview_questions": ["请用 STAR 讲一次 CDC 故障恢复。"],
        },
    )


def document_text(document: Document) -> str:
    parts = [paragraph.text for paragraph in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            for cell in row.cells:
                parts.append(cell.text)
    return "\n".join(parts)


def test_docx_uses_portable_office_fonts_and_highlights_score() -> None:
    document = Document(BytesIO(build_docx([make_evaluation()])))  # type: ignore[list-item]
    text = document_text(document)
    east_asia = document.styles["Normal"].element.rPr.rFonts.get(qn("w:eastAsia"))

    assert east_asia == "微软雅黑"
    assert document.styles["Normal"].font.name == "Calibri"
    assert "Aptos" not in (document.styles["Normal"].font.name or "")
    assert "综合匹配" in text
    assert "86" in text
    assert "高匹配" in text
    assert "硬性" in text
    assert "加分项" in text
    assert "张晨.pdf" in text
    assert "请用 STAR 讲一次 CDC 故障恢复。" in text
    assert document.tables
    assert document.sections[0].footer.paragraphs[0].text


def test_docx_does_not_embed_newlines_inside_a_single_run() -> None:
    document = Document(BytesIO(build_docx([make_evaluation()])))  # type: ignore[list-item]
    for paragraph in document.paragraphs:
        for run in paragraph.runs:
            assert "\n" not in run.text
