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


def test_effective_skills_expected_sets():
    assert effective_skills("student") == ["web_search"]
    assert effective_skills("parent") == ["weekly_report", "diagnose_barrier"]
    tutor = effective_skills("tutor")
    assert set(tutor) == {"search_exam_bank", "web_search", "show_exam_workbench"}
    teacher = effective_skills("teacher")
    assert "diagnose_barrier" in teacher
    assert "assign_adaptive_practice" in teacher
    assert "show_students" in teacher
    assert "delete_bank" in teacher  # 审批工具需教师可达（doc 30 §4.3 矩阵，delete_bank 需审批）
    assert "generate_questions" in teacher


def test_teacher_cannot_access_unknown():
    """teacher 白名单不越过 TOOL_META 授权集。"""
    teacher = set(effective_skills("teacher"))
    # teacher 无权出题组外的工具
    assert "weekly_report" not in teacher
    assert "send_learning_plan" not in teacher


def test_validate_personas_no_hard_problems():
    problems = validate_personas()
    assert problems == [], f"Persona 校验存在硬性问题: {problems}"


def test_unknown_skills_are_future_slice_placeholders():
    """chem_skills（chemistry_tutor 等）为后续切片占位，属未注册 → unknown 告警。"""
    unknown = set(unknown_skills("student"))
    assert "chemistry_tutor" in unknown
    # 占位工具不进入 effective_skills（不注入 LLM）
    assert "chemistry_tutor" not in effective_skills("student")


def test_effective_skills_returns_sorted_whitelist():
    """交集结果无重复且与白名单顺序一致。"""
    persona = load_persona("tutor")
    allowed = set(tools_for_persona("tutor"))
    expected = [s for s in persona.available_skills if s in allowed]
    assert effective_skills("tutor") == expected
