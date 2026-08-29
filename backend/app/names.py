"""从 BOSS 文件名和评估结果中得到可展示的候选人姓名。"""

from __future__ import annotations

import re
from pathlib import Path

_BOSS_NAME = re.compile(r"】\s*(.+?)\s*[_\s]+(?:\d+(?:\.\d+)?年|10年以上)")
_ANONYMOUS = re.compile(r"^(无无|未识别|先生|女士|小姐|[\u4e00-\u9fff]{1,2}(?:先生|女士|小姐))$")


def display_name_from_filename(filename: str) -> str | None:
    """从 BOSS 直聘文件名取出姓名；完全匿名的「无无」返回空。"""

    match = _BOSS_NAME.search(Path(filename).name)
    if not match:
        return None
    name = match.group(1).strip()
    if not name or name == "无无":
        return None
    return name


def is_real_person_name(name: str | None) -> bool:
    """过滤未识别、匿名称呼和空值。"""

    cleaned = (name or "").strip()
    if not cleaned or len(cleaned) > 40:
        return False
    return _ANONYMOUS.match(cleaned) is None


def should_update_display_name(current: str | None, original: str, extracted: str | None) -> bool:
    """仅在当前名称仍是文件名或匿名时，用评估得到的真实姓名覆盖。"""

    if not is_real_person_name(extracted):
        return False
    if not current or current == original:
        return True
    from_file = display_name_from_filename(original)
    if from_file and current == from_file:
        return True
    return not is_real_person_name(current)
