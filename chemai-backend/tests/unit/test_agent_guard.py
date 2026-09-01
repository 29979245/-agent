"""Guard 四层护栏单测（tasks 1.2 / 审查补丁 11.2 执行键登记时机 / 课程验收点强化）。"""
import time

import pytest

from app.agents.guard import APPROVAL_TOOLS, GuardState, build_guard_config, strip_special_fields


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


# ---------------- 课程验收点强化：L1 上下文 / L2 Token Bucket+并发 / L3 窗口 ----------------

def test_l1_context_missing_user_blocked():
    """L1：ToolContext 缺 user 身份或角色 → missing_prerequisites（复用错误码）。"""
    g = GuardState()
    assert g.check("list_banks", {}, ctx=None).ok is True  # 直调无上下文不拦截
    assert g.check("list_banks", {}, ctx=type("C", (), {"user": None})()).error_code == "missing_prerequisites"
    assert g.check("list_banks", {}, ctx=type("C", (), {"user": {"user_id": 1, "role": ""}})()).error_code == "missing_prerequisites"
    assert g.check("list_banks", {}, ctx=type("C", (), {"user": {"role": "teacher"}})()).error_code == "missing_prerequisites"  # 缺身份
    assert g.check("list_banks", {}, ctx=type("C", (), {"user": {"user_id": 1, "role": "teacher"}})()).ok is True
    assert g.check("list_banks", {}, ctx=type("C", (), {"user": type("U", (), {"user_id": 9, "role": "teacher"})()})()).ok is True  # 对象型身份齐全
    assert g.check("list_banks", {}, ctx=type("C", (), {"user": type("U", (), {"role": "teacher"})()})()).error_code == "missing_prerequisites"  # 对象型缺身份


def test_l2_token_bucket_exhaustion_returns_limit_exceeded():
    """L2 Token Bucket：初始满桶，消耗后令牌耗尽 → limit_exceeded。"""
    g = GuardState(token_rates={"web_search": 0.1}, token_capacity={"web_search": 1})
    assert g.check_limit("web_search").ok is True  # 初始满桶 1 枚
    g.record_call("web_search")  # 消耗唯一令牌
    r = g.check_limit("web_search")
    assert r.ok is False
    assert r.error_code == "limit_exceeded"


def test_l2_concurrency_over_limit_blocked():
    """L2 并发在途：超过 max_concurrent → limit_exceeded；结束归还后放行。"""
    g = GuardState(max_concurrent={"web_search": 1})
    assert g.check_limit("web_search").ok is True
    g.begin_execution("web_search")
    r = g.check_limit("web_search")
    assert r.ok is False
    assert r.error_code == "limit_exceeded"
    g.end_execution("web_search")
    assert g.check_limit("web_search").ok is True


def test_l3_dedup_window_expiry_allows_retry():
    """L3 去重窗口：窗口过期后相同调用不再判重、可再次执行。"""
    g = GuardState(dedup_window_s=60.0)
    g.register_execution("list_banks", {})
    assert g.is_duplicate("list_banks", {}) is True
    now = time.monotonic()
    key = g._execution_key("list_banks", {})
    g._execution_keys[key] = now - 61.0  # 拨回窗口之前
    assert g.is_duplicate("list_banks", {}) is False
    assert g.check("list_banks", {}).ok is True


def test_build_guard_config_wires_tool_meta_l2():
    """L2 生产接线：build_guard_config 收纳 TOOL_META 声明的 token/并发配置，未声明沿用默认。"""

    class _Bucketed:
        call_limit = 2
        token_rate = 5.0
        token_capacity = 4
        max_concurrent = 2

    class _Plain:
        call_limit = 3
        token_rate = None
        token_capacity = None
        max_concurrent = None

    cfg = build_guard_config({"bucketed": _Bucketed(), "plain": _Plain()})
    assert cfg == {
        "call_limits": {"bucketed": 2, "plain": 3},
        "token_rates": {"bucketed": 5.0},
        "token_capacity": {"bucketed": 4},
        "max_concurrent": {"bucketed": 2},
    }
    g = GuardState(**cfg)  # 构造有效，证明接线可实例化
    assert g._concurrency_limit("bucketed") == 2
    assert g._concurrency_limit("plain") == 1

    # 真实 TOOL_META 集成：web_search 声明 max_concurrent=2，其余不声明 → 默认 1
    from app.agents.tools.tool_meta import TOOL_META

    real = build_guard_config(TOOL_META)
    assert len(real["call_limits"]) == 38
    assert real["max_concurrent"]["web_search"] == 2
    assert real["max_concurrent"].get("diagnose_barrier") is None  # 未声明不收纳


def test_strip_special_fields():
    result = {"rows": [1], "_component": {"type": "exam-workbench"}, "_route": {"page": "students"}}
    clean, directives = strip_special_fields(result)
    assert clean == {"rows": [1]}
    assert directives["component"] == {"type": "exam-workbench"}
    assert directives["route"] == {"page": "students"}


def test_strip_special_fields_non_dict_passthrough():
    assert strip_special_fields([1, 2]) == ([1, 2], {})
