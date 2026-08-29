"""TOOL_META 注册表完整性单测（tasks 1.3）。"""
from app.agents.tools.tool_meta import (
    TOOL_META,
    integrity_check,
    register_tool,
    ToolMeta,
    tools_for_persona,
)


def test_registered_tools_covered_by_meta():
    problems = integrity_check()
    assert problems == []


def test_slice1_has_14_tools():
    assert len(TOOL_META) == 14


def test_required_tools_present():
    names = set(TOOL_META)
    assert {
        "search_exam_bank", "web_search", "show_exam_workbench", "generate_questions",
        "save_to_bank", "list_banks", "delete_bank",
        "diagnose_barrier", "show_diagnosis", "show_students", "weekly_report",
        "assign_adaptive_practice", "generate_learning_plan", "send_learning_plan",
    } <= names


def test_approval_tools_marked():
    assert TOOL_META["delete_bank"].approval is True
    assert TOOL_META["assign_adaptive_practice"].approval is False  # preview-only，审批移至 API（D2）
    assert TOOL_META["web_search"].approval is False


def test_call_limits_match_doc():
    assert TOOL_META["search_exam_bank"].call_limit == 3
    assert TOOL_META["web_search"].call_limit == 2
    assert TOOL_META["generate_questions"].call_limit == 5
    assert TOOL_META["save_to_bank"].call_limit == 1
    assert TOOL_META["delete_bank"].call_limit == 1
    assert TOOL_META["assign_adaptive_practice"].call_limit == 1


def test_persona_filter():
    teacher_tools = set(tools_for_persona("teacher"))
    assert "diagnose_barrier" in teacher_tools
    assert "web_search" in teacher_tools
    parent_tools = set(tools_for_persona("parent"))
    assert parent_tools <= {"weekly_report", "diagnose_barrier", "web_search"}
    # 学生 slice-1 仅 web_search 开放（其余 tutoring 工具未在 slice-1 注册）
    student_tools = set(tools_for_persona("student"))
    assert student_tools == {"web_search"}


def test_integrity_catches_bad_persona():
    register_tool(ToolMeta(
        name="_test_bad", title="x", description="x",
        personas=("ghost",), call_limit=1,
    ))
    problems = integrity_check()
    assert any("ghost" in p for p in problems)
    TOOL_META.pop("_test_bad")
