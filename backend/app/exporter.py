"""将筛选结果安全导出为招聘人员可继续处理的表格与文档。"""

from __future__ import annotations

import csv
from io import BytesIO, StringIO
import json
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor

from .models import Evaluation


HEADERS = [
    "候选人",
    "简历文件",
    "筛选日期",
    "总分",
    "背景加分",
    "匹配等级",
    "置信度",
    "维度评分",
    "评估摘要",
    "优势",
    "缺口",
    "风险提示",
    "面试问题",
    "证据摘要",
    "人工状态",
    "招聘备注",
    "处理状态",
    "错误信息",
]


def safe_cell(value: Any) -> Any:
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def compact_number(value: Any) -> str:
    """避免在文档中把整数贡献分显示为 23.0。"""

    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def criterion_score_label(criterion: dict[str, Any]) -> str:
    """同时展示百分制能力分与后端计算的加权贡献。"""

    score = criterion.get("score")
    if score is None:
        return "待重新评分"
    label = f"{score}/100"
    weight = criterion.get("weight") or 0
    if weight:
        label += f"（贡献 {compact_number(criterion.get('weighted_score', 0))}/{weight}）"
    return label


def evaluation_row(item: Evaluation) -> list[Any]:
    result = item.result_json or {}
    candidate = result.get("candidate") or {}
    evidence = []
    dimension_scores = []
    for criterion in result.get("criteria") or []:
        dimension_scores.append(f"{criterion.get('criterion', '')}: {criterion_score_label(criterion)}")
        snippets = "；".join(
            f"{entry.get('text', '')}（{entry.get('location', '')}）" for entry in criterion.get("evidence") or []
        )
        evidence.append(f"{criterion.get('criterion', '')}: {snippets}")
    row = [
        candidate.get("name") or "未识别",
        item.resume.original_name,
        item.run.created_at.isoformat(),
        result.get("overall_score"),
        result.get("background_bonus", 0),
        {"high": "高", "medium": "中", "low": "低"}.get(result.get("match_level"), ""),
        result.get("confidence"),
        "；".join(dimension_scores),
        result.get("summary") or "",
        "；".join(result.get("strengths") or []),
        "；".join(result.get("gaps") or []),
        "；".join(result.get("risk_flags") or []),
        "；".join(result.get("interview_questions") or []),
        "\n".join(evidence),
        {"pending": "待复核", "advance": "推进", "reject": "淘汰", "hold": "保留"}.get(item.review_status, item.review_status),
        item.review_note,
        item.status,
        item.error or "",
    ]
    return [safe_cell(value) for value in row]


def build_xlsx(items: list[Evaluation]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "筛选结果"
    sheet.append(HEADERS)
    for item in items:
        sheet.append(evaluation_row(item))
    header_fill = PatternFill("solid", fgColor="173F35")
    for cell in sheet[1]:
        cell.fill = header_fill
        cell.font = Font(color="FFFFFF", bold=True)
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    widths = [16, 24, 22, 9, 10, 10, 10, 36, 36, 30, 30, 30, 34, 48, 12, 28, 12, 30]
    for index, width in enumerate(widths, 1):
        sheet.column_dimensions[chr(64 + index)].width = width
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def build_csv(items: list[Evaluation]) -> bytes:
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(HEADERS)
    writer.writerows(evaluation_row(item) for item in items)
    return ("\ufeff" + output.getvalue()).encode("utf-8")


def build_json(items: list[Evaluation]) -> bytes:
    """导出保留完整结构的机器可读评估结果。"""

    payload = {
        "schema_version": "2",
        "items": [
            {
                "evaluation_id": item.id,
                "run_id": item.run_id,
                "resume_id": item.resume_id,
                "resume_name": item.resume.original_name,
                "screened_at": item.run.created_at.isoformat(),
                "status": item.status,
                "attempts": item.attempts,
                "review_status": item.review_status,
                "review_note": item.review_note,
                "updated_at": item.updated_at.isoformat(),
                "result": item.result_json,
                "error": item.error,
            }
            for item in items
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


def display_value(value: Any, fallback: str = "—") -> str:
    """把空值转换为适合人类阅读的文档文本。"""

    if value is None or value == "":
        return fallback
    return str(value)


def candidate_name(item: Evaluation) -> str:
    result = item.result_json or {}
    return (result.get("candidate") or {}).get("name") or item.resume.original_name


def localized_match_level(value: str | None) -> str:
    return {"high": "高", "medium": "中", "low": "低"}.get(value or "", "未评分")


def localized_review_status(value: str) -> str:
    return {"pending": "待复核", "advance": "推进", "reject": "淘汰", "hold": "保留"}.get(value, value)


def markdown_list(items: list[str] | None) -> str:
    values = items or []
    return "\n".join(f"- {value}" for value in values) if values else "- 无"


def build_markdown(items: list[Evaluation]) -> bytes:
    """生成保留评估层次与证据的 Markdown 分析报告。"""

    sections = ["# 候选人筛选分析", "", f"> 共导出 {len(items)} 位候选人的筛选结果。"]
    for index, item in enumerate(items, 1):
        result = item.result_json or {}
        candidate = result.get("candidate") or {}
        sections.extend(
            [
                "",
                "---",
                "",
                f"## {index}. {candidate_name(item)}",
                "",
                f"- 简历文件：{item.resume.original_name}",
                f"- 筛选日期：{item.run.created_at.isoformat()}",
                f"- 当前岗位：{display_value(candidate.get('current_role'))}",
                f"- 综合得分：{display_value(result.get('overall_score'), '未评分')}",
                f"- 背景加分：{display_value(result.get('background_bonus'), '0')}",
                f"- 评分版本：{display_value(result.get('scoring_version'), '旧版')}",
                f"- 匹配等级：{localized_match_level(result.get('match_level'))}",
                f"- 置信度：{display_value(result.get('confidence'))}",
                f"- 人工状态：{localized_review_status(item.review_status)}",
                f"- 招聘备注：{display_value(item.review_note)}",
                "",
                "### 评估摘要",
                "",
                display_value(result.get("summary"), "尚未生成评估摘要。"),
                "",
                "### 维度评分与证据",
            ]
        )
        criteria = result.get("criteria") or []
        if not criteria:
            sections.extend(["", "尚未生成维度评分。"])
        for criterion in criteria:
            sections.extend(
                [
                    "",
                    f"#### {criterion.get('criterion') or '未命名维度'} · {criterion_score_label(criterion)}",
                ]
            )
            evidence = criterion.get("evidence") or []
            if evidence:
                for entry in evidence:
                    sections.extend(
                        [
                            "",
                            f"> {entry.get('text') or '无证据文本'}",
                            ">",
                            f"> 位置：{entry.get('location') or '未标注'}",
                        ]
                    )
            else:
                sections.extend(["", "未提供原文证据。"])
        for title, key in [
            ("优势", "strengths"),
            ("缺口", "gaps"),
            ("风险提示", "risk_flags"),
            ("建议面试追问", "interview_questions"),
        ]:
            sections.extend(["", f"### {title}", "", markdown_list(result.get(key))])
        if item.error:
            sections.extend(["", "### 处理错误", "", item.error])
    return ("\n".join(sections) + "\n").encode("utf-8")


def set_run_font(run: Any, size: int | None = None, bold: bool | None = None, color: RGBColor | None = None) -> None:
    """统一 Word 文档中的中西文字体。"""

    run.font.name = "Aptos"
    run._element.rPr.rFonts.set(qn("w:eastAsia"), "思源宋体")
    if size is not None:
        run.font.size = Pt(size)
    if bold is not None:
        run.bold = bold
    if color is not None:
        run.font.color.rgb = color


def add_bullet_list(document: Document, values: list[str] | None) -> None:
    for value in values or ["无"]:
        document.add_paragraph(value, style="List Bullet")


def build_docx(items: list[Evaluation]) -> bytes:
    """生成适合归档和人工传阅的 Word 分析报告。"""

    document = Document()
    styles = document.styles
    styles["Normal"].font.name = "Aptos"
    styles["Normal"]._element.rPr.rFonts.set(qn("w:eastAsia"), "思源宋体")
    styles["Normal"].font.size = Pt(10.5)
    for style_name in ["Title", "Heading 1", "Heading 2", "Heading 3"]:
        styles[style_name].font.name = "Aptos Display"
        styles[style_name]._element.rPr.rFonts.set(qn("w:eastAsia"), "思源宋体")

    title = document.add_heading("候选人筛选分析", 0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    subtitle = document.add_paragraph(f"共导出 {len(items)} 位候选人的筛选结果")
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
    for run in subtitle.runs:
        set_run_font(run, size=10, color=RGBColor(95, 107, 101))

    for index, item in enumerate(items, 1):
        result = item.result_json or {}
        candidate = result.get("candidate") or {}
        document.add_heading(f"{index}. {candidate_name(item)}", level=1)
        metadata = document.add_table(rows=0, cols=2)
        metadata.style = "Light Shading Accent 1"
        for label, value in [
            ("简历文件", item.resume.original_name),
            ("筛选日期", item.run.created_at.isoformat()),
            ("当前岗位", display_value(candidate.get("current_role"))),
            ("综合得分", display_value(result.get("overall_score"), "未评分")),
            ("背景加分", display_value(result.get("background_bonus"), "0")),
            ("评分版本", display_value(result.get("scoring_version"), "旧版")),
            ("匹配等级", localized_match_level(result.get("match_level"))),
            ("置信度", display_value(result.get("confidence"))),
            ("人工状态", localized_review_status(item.review_status)),
            ("招聘备注", display_value(item.review_note)),
        ]:
            cells = metadata.add_row().cells
            cells[0].text = label
            cells[1].text = value
            for run in cells[0].paragraphs[0].runs:
                set_run_font(run, bold=True)

        document.add_heading("评估摘要", level=2)
        document.add_paragraph(display_value(result.get("summary"), "尚未生成评估摘要。"))
        document.add_heading("维度评分与证据", level=2)
        criteria = result.get("criteria") or []
        if not criteria:
            document.add_paragraph("尚未生成维度评分。")
        for criterion in criteria:
            document.add_heading(
                f"{criterion.get('criterion') or '未命名维度'} · {criterion_score_label(criterion)}",
                level=3,
            )
            evidence = criterion.get("evidence") or []
            if not evidence:
                document.add_paragraph("未提供原文证据。")
            for entry in evidence:
                paragraph = document.add_paragraph(style="Quote")
                paragraph.add_run(entry.get("text") or "无证据文本")
                paragraph.add_run(f"\n位置：{entry.get('location') or '未标注'}")
        for title_text, key in [
            ("优势", "strengths"),
            ("缺口", "gaps"),
            ("风险提示", "risk_flags"),
            ("建议面试追问", "interview_questions"),
        ]:
            document.add_heading(title_text, level=2)
            add_bullet_list(document, result.get(key))
        if item.error:
            document.add_heading("处理错误", level=2)
            document.add_paragraph(item.error)
        if index < len(items):
            document.add_page_break()

    output = BytesIO()
    document.save(output)
    return output.getvalue()
