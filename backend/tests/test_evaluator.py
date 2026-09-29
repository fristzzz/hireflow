"""验证模型评估器对不同结构化输出协议的兼容性。"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from openai import BadRequestError

from app.config import Settings
from app.evaluator import OpenAIEvaluator, compile_scoring_dimensions


def evaluation_payload() -> dict[str, Any]:
    """返回满足评估卡约束的最小模型响应。"""

    return {
        "candidate": {"name": "候选人"},
        "confidence": 0.9,
        "summary": "经历与岗位要求匹配。",
        "criteria": [
            {
                "criterion_id": "D01",
                "score": 80,
                "status": "met",
                "evidence": [{"text": "具有相关经历", "location": "段落 1"}],
            }
        ],
        "background_highlights": [
            {
                "kind": "company",
                "name": "头部科技公司",
                "bonus_points": 2,
                "evidence": [{"text": "在头部科技公司负责数据平台", "location": "段落 2"}],
            }
        ],
        "strengths": ["相关经验充分"],
        "gaps": [],
        "risk_flags": [],
        "interview_questions": ["请介绍一个代表性项目。"],
    }


def bad_request(message: str) -> BadRequestError:
    """构造与兼容接口返回的 400 异常一致的 SDK 错误。"""

    request = httpx.Request("POST", "https://api.deepseek.com/chat/completions")
    response = httpx.Response(400, request=request)
    return BadRequestError(message, response=response, body={"error": {"message": message}})


class DeepSeekCompletions:
    """模拟仅支持 json_object 且检查提示词的 DeepSeek 接口。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        response_format = kwargs["response_format"]
        if response_format["type"] != "json_object":
            raise bad_request("response_format 只支持 text 或 json_object")

        prompt_text = "\n".join(message["content"] for message in kwargs["messages"])
        if "json" not in prompt_text.lower():
            raise bad_request(
                "Prompt must contain the word 'json' in some form to use "
                "'response_format' of type 'json_object'."
            )
        assert '"criterion_id"' in prompt_text
        assert '"score"' in prompt_text
        assert '"weight":100' in prompt_text
        message = SimpleNamespace(content=json.dumps(evaluation_payload(), ensure_ascii=False))
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class FakeClient:
    """提供与 AsyncOpenAI 调用路径相同的测试替身。"""

    def __init__(self, completions: DeepSeekCompletions) -> None:
        self.chat = SimpleNamespace(completions=completions)


@pytest.mark.asyncio
async def test_deepseek_directly_uses_json_object_with_json_schema_in_prompt() -> None:
    settings = Settings(
        openai_api_key="test-key",
        openai_base_url="https://api.deepseek.com",
        text_model="deepseek-v4-flash",
    )
    evaluator = OpenAIEvaluator(settings)
    completions = DeepSeekCompletions()
    evaluator._client = FakeClient(completions)  # type: ignore[assignment]

    result = await evaluator.evaluate(
        jd_text="招聘 Python 后端工程师",
        prompt="## 100分评分标准\n\n### 1. 工程能力：100分",
        resume_text="候选人具有五年 Python 经验。",
    )

    assert result.scoring_version == "2"
    assert result.overall_score == 82
    assert result.match_level == "medium"
    assert result.background_bonus == 2
    assert result.criteria[0].criterion == "工程能力"
    assert result.criteria[0].weight == 100
    assert result.criteria[0].score == 80
    assert result.criteria[0].weighted_score == 80
    assert result.background_highlights[0].name == "头部科技公司"
    assert result.background_highlights[0].bonus_points == 2
    assert len(completions.calls) == 1
    assert completions.calls[0]["response_format"] == {"type": "json_object"}


class EmptyThenSuccessCompletions:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            message = SimpleNamespace(content="")
            return SimpleNamespace(
                choices=[SimpleNamespace(message=message, finish_reason="stop")]
            )
        message = SimpleNamespace(content=json.dumps(evaluation_payload(), ensure_ascii=False))
        return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")])


class AlwaysEmptyCompletions:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=None), finish_reason="length")]
        )


@pytest.mark.asyncio
async def test_empty_model_content_retries_with_truncated_resume() -> None:
    settings = Settings(
        openai_api_key="test-key",
        openai_base_url="https://api.deepseek.com",
        text_model="deepseek-v4-flash",
    )
    evaluator = OpenAIEvaluator(settings)
    completions = EmptyThenSuccessCompletions()
    evaluator._client = FakeClient(completions)  # type: ignore[assignment]
    long_resume = "候选人具有五年 Python 经验。" * 4000

    result = await evaluator.evaluate(
        jd_text="招聘 Python 后端工程师",
        prompt="## 100分评分标准\n\n### 1. 工程能力：100分",
        resume_text=long_resume,
    )

    assert result.overall_score == 82
    assert len(completions.calls) == 2
    second_prompt = completions.calls[1]["messages"][1]["content"]
    assert len(second_prompt) < len(completions.calls[0]["messages"][1]["content"])


@pytest.mark.asyncio
async def test_empty_model_content_is_evaluation_failure_not_parse_failure() -> None:
    settings = Settings(
        openai_api_key="test-key",
        openai_base_url="https://api.deepseek.com",
        text_model="deepseek-v4-flash",
    )
    evaluator = OpenAIEvaluator(settings)
    completions = AlwaysEmptyCompletions()
    evaluator._client = FakeClient(completions)  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="不是简历文件解析失败"):
        await evaluator.evaluate(
            jd_text="招聘 Python 后端工程师",
            prompt="## 100分评分标准\n\n### 1. 工程能力：100分",
            resume_text="候选人具有五年 Python 经验。",
        )
    assert len(completions.calls) == 2


def test_compile_scoring_dimensions_from_recruiter_rubric() -> None:
    prompt = """
## 五、100分评分标准

### 1. 领域匹配度：25分
### 2. 技术动手能力：20分
### 3. 数据正确性与故障测试：20分
### 4. 测试架构与平台建设：15分
### 5. 生产事故经验：10分
### 6. 影响力和质量推动能力：10分
"""

    dimensions = compile_scoring_dimensions(prompt)

    assert [(item.criterion_id, item.name, item.weight) for item in dimensions] == [
        ("D01", "领域匹配度", 25),
        ("D02", "技术动手能力", 20),
        ("D03", "数据正确性与故障测试", 20),
        ("D04", "测试架构与平台建设", 15),
        ("D05", "生产事故经验", 10),
        ("D06", "影响力和质量推动能力", 10),
    ]


def test_compile_scoring_dimensions_uses_stable_fallback() -> None:
    dimensions = compile_scoring_dimensions("重点考察工程能力")

    assert sum(item.weight for item in dimensions) == 100
    assert [item.criterion_id for item in dimensions] == ["D01", "D02", "D03", "D04", "D05", "D06"]
    assert all(not item.knockout for item in dimensions)


def test_compile_scoring_dimensions_from_markdown_table() -> None:
    prompt = """
## 评分标准

| 评分项 | 权重 | 说明 |
| --- | --- | --- |
| CDC 同步引擎能力 | 30分 | 要有引擎开发，不只是使用 |
| 数据建模与分层 | 25 | 数仓分层与主题域 |
| 实时架构设计 | 20 | 延迟与一致性 |
| 领域加分项 | 15 | 金融或安全背景 |
| 工程落地 | 10 | 代码与交付 |
"""

    dimensions = compile_scoring_dimensions(prompt)

    assert [(item.name, item.weight, item.kind) for item in dimensions] == [
        ("CDC 同步引擎能力", 30, "core"),
        ("数据建模与分层", 25, "core"),
        ("实时架构设计", 20, "core"),
        ("领域加分项", 15, "bonus"),
        ("工程落地", 10, "core"),
    ]


def test_compile_scoring_dimensions_from_wide_markdown_table() -> None:
    prompt = """
| 评估维度 | 权重 | 关注能力与考察重点 | 评分标准 |
| :--- | :--- | :--- | :--- |
| **CDC 与实时计算** | 30% | Flink CDC | • **25-30分**：源码级定制 |
| **数据引擎与建模** | 25% | Doris | • **20-25分**：湖仓建模 |
| **架构与稳定性** | 20% | 链路 | • **16-20分**：主导架构 |
| **加分项与场景契合** | 15% | 金融 | • **12-15分**：跨境 |
| **基础工程与通用素养** | 10% | Java | • **8-10分**：开源 |
"""

    dimensions = compile_scoring_dimensions(prompt)

    assert [(item.name, item.weight, item.kind) for item in dimensions] == [
        ("CDC 与实时计算", 30, "core"),
        ("数据引擎与建模", 25, "core"),
        ("架构与稳定性", 20, "core"),
        ("加分项与场景契合", 15, "bonus"),
        ("基础工程与通用素养", 10, "core"),
    ]


def test_compile_scoring_dimensions_rejects_unparsed_weight_text() -> None:
    prompt = "评分标准：CDC 30分，建模 25分，架构 20分，加分 15分，工程 10分。"

    with pytest.raises(RuntimeError, match="无法识别"):
        compile_scoring_dimensions(prompt)


def test_compile_scoring_dimensions_marks_knockout_from_heading() -> None:
    prompt = """
### 1. Java 工程能力：80分
### 2. CDC 引擎（硬性）：20分
"""

    dimensions = compile_scoring_dimensions(prompt)

    assert [(item.name, item.weight, item.kind, item.knockout) for item in dimensions] == [
        ("Java 工程能力", 80, "core", False),
        ("CDC 引擎", 20, "knockout", True),
    ]


def test_compile_scoring_profile_overrides_must_have_and_bonus() -> None:
    prompt = """
### 1. CDC 同步引擎能力：30分
### 2. 数据建模与分层：25分
### 3. 实时架构设计：20分
### 4. 领域加分项：15分
### 5. 工程落地：10分
"""
    dimensions = compile_scoring_dimensions(
        prompt,
        [
            {"name": "CDC 同步引擎能力", "weight": 30, "kind": "knockout"},
            {"name": "数据建模与分层", "weight": 25, "kind": "core"},
            {"name": "实时架构设计", "weight": 20, "kind": "core"},
            {"name": "领域加分项", "weight": 15, "kind": "bonus"},
            {"name": "工程落地", "weight": 10, "kind": "core"},
        ],
    )

    assert [(item.name, item.kind) for item in dimensions] == [
        ("CDC 同步引擎能力", "knockout"),
        ("数据建模与分层", "core"),
        ("实时架构设计", "core"),
        ("领域加分项", "bonus"),
        ("工程落地", "core"),
    ]


@pytest.mark.asyncio
async def test_knockout_not_met_caps_match_level_to_low() -> None:
    settings = Settings(
        openai_api_key="test-key",
        openai_base_url="https://api.deepseek.com",
        text_model="deepseek-v4-flash",
    )
    evaluator = OpenAIEvaluator(settings)
    completions = DeepSeekCompletions()
    payload = evaluation_payload()
    payload["background_highlights"] = []
    payload["criteria"] = [
        {
            "criterion_id": "D01",
            "score": 100,
            "status": "met",
            "evidence": [{"text": "Java 经验充分", "location": "段落 1"}],
        },
        {
            "criterion_id": "D02",
            "score": 10,
            "status": "not_met",
            "evidence": [{"text": "仅使用过同步工具", "location": "段落 2"}],
        },
    ]

    async def create(**kwargs: Any) -> SimpleNamespace:
        completions.calls.append(kwargs)
        message = SimpleNamespace(content=json.dumps(payload, ensure_ascii=False))
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    completions.create = create  # type: ignore[method-assign]
    evaluator._client = FakeClient(completions)  # type: ignore[assignment]

    result = await evaluator.evaluate(
        jd_text="招聘实时数据引擎工程师",
        prompt="### 1. Java 工程能力：80分\n### 2. CDC 引擎（硬性）：20分",
        resume_text="候选人长期使用数据同步产品。",
    )

    assert result.overall_score == 82
    assert result.match_level == "low"
    assert result.criteria[1].knockout is True
    assert result.criteria[1].status == "not_met"
