"""Guard 四层护栏单测（tasks 1.2 / 审查补丁 11.2 执行键登记时机）。"""
import pytest

from app.agents.guard import APPROVAL_TOOLS, GuardState, strip_special_fields


def test_prerequisites_missing_keyword():
    g = GuardState()
    r = g.check_prerequisites("search_exam_bank", {"keyword": "氧"})
    assert r.ok is False
    assert r.error_code == "missing_prerequisites"
    assert g.check_prerequisites("search_exam_bank", {"keyword": "氧化还原"}).ok is True


def test_prerequisites_diagnose_needs_identity():
    g = GuardState()
    assert g.check_prerequisites("diagnose_barrier", {}).ok is False
    assert g.check_prerequisites("diagnose_barrier", {"student_id": 2024001}).ok is True
    assert g.check_prerequisites("diagnose_barrier", {"class_name": "高一一班"}).ok is True


def test_call_limit_exceeded():
    g = GuardState(call_limits={"web_search": 2})
    assert g.check_limit("web_search").ok is True
    g.record_call("web_search")
    g.record_call("web_search")
    r = g.check_limit("web_search")
    assert r.ok is False
    assert r.error_code == "limit_exceeded"


def test_dedup_skips_after_execution_registered():
    """D11：执行键在执行开始登记后，相同调用判重。"""
    g = GuardState()
    args = {"keyword": "氧化还原", "count": 3}
    assert g.is_duplicate("search_exam_bank", args) is False
    g.register_execution("search_exam_bank", args)
    assert g.is_duplicate("search_exam_bank", args) is True


def test_dedup_key_sorts_args():
    g = GuardState()
    a = {"b": 2, "a": 1}
    b = {"a": 1, "b": 2}
    g.register_execution("t", a)
    assert g.is_duplicate("t", b) is True  # 参数顺序无关


def test_approval_tool_blocked_without_approval():
    g = GuardState()
    args = {"bank_name": "高一氧化还原"}
    r = g.check_approval("delete_bank", args)
    assert r.ok is False
    assert r.error_code == "requires_approval_blocked"
    assert r.payload["requires_approval"] is True


def test_approval_tool_passes_after_approved():
    g = GuardState()
    args = {"bank_name": "高一氧化还原"}
    g.mark_approved("delete_bank", args)
    assert g.check_approval("delete_bank", args).ok is True


def test_non_approval_tool_passes_layer4():
    g = GuardState()
    assert g.check_approval("web_search", {}).ok is True


def test_approval_tools_set():
    assert APPROVAL_TOOLS == frozenset({
        "delete_bank",
        "grade_answer_sheets",
        "save_grading_results",
        "send_report_to_parent",
    })


def test_send_report_to_parent_approval_blocked():
    """send_report_to_parent 属审批门控工具，未确认返回 blocked。"""
    g = GuardState()
    r = g.check_approval("send_report_to_parent", {"student_id": 1})
    assert r.ok is False
    assert r.error_code == "requires_approval_blocked"
    g.mark_approved("send_report_to_parent", {"student_id": 1})
    assert g.check_approval("send_report_to_parent", {"student_id": 1}).ok is True


def test_parent_report_student_id_prerequisite():
    """报告两工具 student_id 前置：缺失返回 missing_prerequisites，补全通过。"""
    g = GuardState()
    for name in ("generate_parent_report", "send_report_to_parent"):
        assert g.check_prerequisites(name, {}).error_code == "missing_prerequisites"
        assert g.check_prerequisites(name, {"student_id": 1}).ok is True


def test_ocr_write_tools_approval_blocked():
    """grade_answer_sheets / save_grading_results 属审批门控工具，未确认返回 blocked。"""
    g = GuardState()
    for name in ("grade_answer_sheets", "save_grading_results"):
        r = g.check_approval(name, {"batch_id": 1, "exam_id": 2})
        assert r.ok is False
        assert r.error_code == "requires_approval_blocked"
        assert r.payload["requires_approval"] is True
        g.mark_approved(name, {"batch_id": 1, "exam_id": 2})
        assert g.check_approval(name, {"batch_id": 1, "exam_id": 2}).ok is True


def test_ocr_batch_prerequisite():
    """OCR 三工具 batch_id 前置：缺失返回 missing_prerequisites，补全通过。"""
    g = GuardState()
    for name in ("query_ocr_progress", "grade_answer_sheets", "save_grading_results"):
        assert g.check_prerequisites(name, {}).error_code == "missing_prerequisites"
        assert g.check_prerequisites(name, {"batch_id": 1}).ok is True


def test_assign_adaptive_practice_no_longer_approval():
    """design D2：assign_adaptive_practice 改为 preview-only，不再进入审批门控。"""
    g = GuardState()
    assert g.check_approval("assign_adaptive_practice", {"class_id": 1}).ok is True


def test_full_check_pipeline():
    g = GuardState(call_limits={"web_search": 1})
    assert g.check("web_search", {"q": "什么"}) is not None


def test_strip_special_fields():
    result = {"rows": [1], "_component": {"type": "exam-workbench"}, "_route": {"page": "students"}}
    clean, directives = strip_special_fields(result)
    assert clean == {"rows": [1]}
    assert directives["component"] == {"type": "exam-workbench"}
    assert directives["route"] == {"page": "students"}


def test_strip_special_fields_non_dict_passthrough():
    assert strip_special_fields([1, 2]) == ([1, 2], {})
