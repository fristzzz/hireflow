"""把内部异常映射为可展示给用户的稳定文案。"""

from __future__ import annotations

from .documents import DocumentError

# 已知、可直接展示的业务错误关键词。
_SAFE_MARKERS = (
    "未配置",
    "权重",
    "维度",
    "模型",
    "不存在",
    "不能",
    "无法",
    "仅支持",
    "超过",
    "为空",
    "加密",
    "无效",
    "不一致",
    "请重试",
    "请先",
    "扫描",
    "OCR",
)


def public_failure_message(exc: BaseException, fallback: str = "处理失败，请重试") -> str:
    """返回适合写回 API / 任务错误字段的文案，避免泄漏路径与供应商细节。"""

    if isinstance(exc, DocumentError):
        return str(exc)[:2000]
    message = str(exc).strip()
    if not message:
        return fallback
    if isinstance(exc, RuntimeError) and any(marker in message for marker in _SAFE_MARKERS):
        return message[:2000]
    if isinstance(exc, ValueError) and any(marker in message for marker in _SAFE_MARKERS):
        return message[:2000]
    return fallback
