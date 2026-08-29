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
