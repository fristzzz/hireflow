"""验证 BOSS 文件名与评估姓名的展示规则。"""

from __future__ import annotations

from app.names import display_name_from_filename, is_real_person_name, should_update_display_name


def test_boss_filename_extracts_candidate_name() -> None:
    assert display_name_from_filename("【测试工程师_深圳_28-50K】江贺琨_15年.pdf") == "江贺琨"
    assert display_name_from_filename("【资深JAVA工程师-实时数据引擎_深圳 35-55K】许庆煌 5年.pdf") == "许庆煌"
    assert display_name_from_filename("【资深JAVA工程师-实时数据引擎_深圳_35-55K】Jaemon_11年.pdf") == "Jaemon"


def test_anonymous_boss_filename_is_not_treated_as_real_name() -> None:
    assert display_name_from_filename("【资深JAVA工程师-实时数据引擎_深圳 35-55K】无无 3年.pdf") is None
    assert display_name_from_filename("【数据架构专家_深圳_35-55K】廖先生_11年.pdf") == "廖先生"
    assert not is_real_person_name("无无")
    assert not is_real_person_name("未识别")
    assert not is_real_person_name("廖先生")
    assert is_real_person_name("罗振羽")


def test_evaluation_name_replaces_filename_or_anonymous_display() -> None:
    original = "【资深JAVA工程师-实时数据引擎_深圳 35-55K】无无 3年.pdf"
    jaemon = "【资深JAVA工程师-实时数据引擎_深圳_35-55K】Jaemon_11年.pdf"
    assert should_update_display_name(original, original, "罗振羽")
    assert should_update_display_name("Jaemon", jaemon, "林建明")
    assert not should_update_display_name("林川-人工修正版.docx", original, "罗振羽")
    assert not should_update_display_name("无无 3年.pdf", original, "未识别")
