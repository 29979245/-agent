"""Persona 加载与校验单测（doc 30 §4.2 / spec Persona 工具过滤，tasks 对话闭环）。

- effective_skills = YAML available_skills ∩ TOOL_META 该角色可用工具（交集，有序）。
- validate_personas：交集非空、无越权工具（已注册但不在该角色集）→ 硬性问题为空。
- 未注册的化学技能（chem_skills 后续切片占位）进入 unknown_skills，仅告警非错误。
"""
import pytest

from app.agents.personas.loader import (
    PersonaLoadError,
    effective_skills,
    load_persona,
    unknown_skills,
    validate_personas,
)
from app.agents.tools.tool_meta import TOOL_META, tools_for_persona

ALL_PERSONAS = ("teacher", "student", "tutor", "parent")


def test_all_personas_load():
    for name in ALL_PERSONAS:
        persona = load_persona(name)
        assert persona.name == name
        assert persona.system_prompt.strip(), f"{name} 缺 system_prompt"
        assert persona.available_skills, f"{name} 白名单为空"


def test_load_unknown_persona_raises():
    with pytest.raises(PersonaLoadError):
        load_persona("bogus")


def test_effective_skills_intersection_known():
    """交集工具必须已注册且属于该角色 TOOL_META 集。"""
    for name in ALL_PERSONAS:
        skills = effective_skills(name)
        assert skills, f"{name} 交集为空"
        allowed = set(tools_for_persona(name))
        for s in skills:
            assert s in TOOL_META, f"{name} 含未注册工具: {s}"
            assert s in allowed, f"{name} 越权工具: {s}"
        # 交集保持白名单相对顺序
        persona = load_persona(name)
        whitelist = persona.available_skills
        assert [s for s in whitelist if s in allowed] == skills


BROWSER = {"browse_navigate", "browse_read", "browse_click", "browse_input", "browse_screenshot"}


def test_effective_skills_expected_sets():
    # 学生开放 4 专题 + 通用辅导 + 实验模拟 + 联网搜索 + 自读记忆（periodic_law/organic 注册但按 §4.2 不进白名单）+ 浏览器
    assert set(effective_skills("student")) == {
        "chemistry_tutor", "simulate_experiment", "web_search",
        "ionic_equation_tutor", "stoichiometry_tutor", "redox_tutor", "equilibrium_tutor",
        "memory_student_get",
    } | BROWSER
    # 全体角色可用记忆（TOOL_META PERSONAS），家长/导师补入 memory_student_get + 浏览器
    assert set(effective_skills("parent")) == {"weekly_report", "diagnose_barrier", "memory_student_get"} | BROWSER
    tutor = effective_skills("tutor")
    assert set(tutor) == {"chemistry_tutor", "search_exam_bank", "web_search",
                          "show_exam_workbench", "simulate_experiment", "balance_equation",
                          "memory_student_get"} | BROWSER
    teacher = effective_skills("teacher")
    assert "diagnose_barrier" in teacher
    assert "assign_adaptive_practice" in teacher
    assert "show_students" in teacher
    assert "delete_bank" in teacher  # 审批工具需教师可达（doc 30 §4.3 矩阵，delete_bank 需审批）
    assert "generate_questions" in teacher
    # 本轮补入：OCR 3 + 记忆 2 + 家长报告 2
    for t in ("query_ocr_progress", "grade_answer_sheets", "save_grading_results",
              "memory_student_get", "memory_teacher_get",
              "generate_parent_report", "send_report_to_parent"):
        assert t in teacher


def test_teacher_cannot_access_unknown():
    """teacher 白名单不越过 TOOL_META 授权集。"""
    teacher = set(effective_skills("teacher"))
    # teacher 无权出题组外的工具
    assert "weekly_report" not in teacher
    assert "send_learning_plan" not in teacher


def test_validate_personas_no_hard_problems():
    problems = validate_personas()
    assert problems == [], f"Persona 校验存在硬性问题: {problems}"


def test_no_unknown_skills_after_registration():
    """辅导/记忆等切片工具已注册：各 Persona 白名单无未注册占位，全部进入 effective_skills。"""
    for name in ALL_PERSONAS:
        unknown = unknown_skills(name)
        assert unknown == [], f"{name} 含未注册工具: {unknown}"
        whitelist = load_persona(name).available_skills
        assert set(effective_skills(name)) == set(whitelist), f"{name} 白名单未全部生效"


def test_effective_skills_returns_sorted_whitelist():
    """交集结果无重复且与白名单顺序一致。"""
    persona = load_persona("tutor")
    allowed = set(tools_for_persona("tutor"))
    expected = [s for s in persona.available_skills if s in allowed]
    assert effective_skills("tutor") == expected
