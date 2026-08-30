"""Agent 工具组 eval（10.1 / 11.2）：注册表完整性 + Guard 四层 + 剥离 + Persona 过滤。

确定性检查（无需 LLM / DB / 网络），供 `run_evals --tier all --compare` 与基线对比：
- 注册表：TOOL_IMPLS == TOOL_SCHEMAS == TOOL_META（35 工具）、integrity_check 无问题；
- Persona：每个 Persona 白名单∩注册工具非空、无越权、student 不含教师专有工具；
- Guard 四层：前置/限次/去重/审批各自返回正确错误码，D11 登记时机双向
  （审批阻塞未启动不登记；执行开始后登记→重试去重跳过，防重放写）；
- 特殊字段剥离：_component/_route 剥离后 LLM 只见纯净业务结果。
"""
from __future__ import annotations

from app.agents.guard import APPROVAL_TOOLS, GuardState, strip_special_fields
from app.agents.personas.loader import effective_skills, validate_personas
from app.agents.tools.registry import TOOL_IMPLS, TOOL_SCHEMAS
from app.agents.tools.tool_meta import TOOL_META, integrity_check

AGENT_TOOLS_PASS_RATE = 0.95


TEACHER_ONLY_TOOLS = {"memory_teacher_get", "query_ocr_progress", "grade_answer_sheets",
                      "save_grading_results", "generate_parent_report", "send_report_to_parent"}

# student persona 白名单（doc 30 §4.2）：辅导 6 专题 + 通用辅导 + 实验模拟 + 联网 + 读自身记忆 + 浏览器 5 工具
STUDENT_TOOLS_EXACT = {
    "chemistry_tutor", "simulate_experiment", "web_search",
    "ionic_equation_tutor", "stoichiometry_tutor", "redox_tutor",
    "equilibrium_tutor", "memory_student_get",
    "browse_navigate", "browse_read", "browse_click", "browse_input", "browse_screenshot",
}

# 浏览器工具组（doc 30 §3.8）：全角色可用
BROWSER_TOOLS = {"browse_navigate", "browse_read", "browse_click", "browse_input", "browse_screenshot"}


def _registry_checks() -> list[tuple[str, bool]]:
    return [
        ("registry_impl_schema_meta_align",
         set(TOOL_IMPLS) == set(TOOL_SCHEMAS) == set(TOOL_META)),
        ("registry_total_is_35", len(TOOL_IMPLS) == 35),
        ("browser_tools_registered_5", BROWSER_TOOLS <= set(TOOL_IMPLS)),
        ("tool_meta_integrity_no_problems", not integrity_check()),
        ("persona_validation_clean", not validate_personas()),
        ("every_persona_has_tools",
         all(effective_skills(p) for p in ("teacher", "student", "tutor", "parent"))),
        ("student_persona_exact_toolset",
         set(effective_skills("student")) == STUDENT_TOOLS_EXACT),
        ("student_persona_no_teacher_only",
         not (set(effective_skills("student")) & TEACHER_ONLY_TOOLS)),
        ("approval_tools_match_meta",
         {n for n, m in TOOL_META.items() if m.approval} == set(APPROVAL_TOOLS)),
        ("all_roles_include_browser_tools",
         all(BROWSER_TOOLS <= set(effective_skills(p)) for p in ("teacher", "student", "tutor", "parent"))),
    ]


def _guard_checks() -> list[tuple[str, bool]]:
    checks: list[tuple[str, bool]] = []

    # L1 前置条件：search_exam_bank keyword ≤2 字符 → missing_prerequisites
    r = GuardState().check("search_exam_bank", {"keyword": "x"})
    checks.append(("l1_prerequisite_missing", r.error_code == "missing_prerequisites"))

    # L2 调用限次：web_search call_limit 2 → 第三次 limit_exceeded
    g = GuardState(call_limits={"web_search": 2})
    g.record_call("web_search")
    g.record_call("web_search")
    r = g.check("web_search", {"query": "化学"})
    checks.append(("l2_limit_exceeded", r.error_code == "limit_exceeded"))

    # L3 去重：执行开始后登记 → 相同调用 dedup_skipped
    g = GuardState()
    g.register_execution("list_banks", {})
    r = g.check("list_banks", {})
    checks.append(("l3_dedup_after_execution", r.error_code == "dedup_skipped"))

    # L4 审批：approval 工具未获批 → requires_approval_blocked + payload
    r = GuardState().check("delete_bank", {"bank_id": 1})
    checks.append(("l4_approval_blocked",
                   r.error_code == "requires_approval_blocked" and r.payload.get("requires_approval") is True))

    # D11 登记时机双向（11.2）：
    # (a) 审批阻塞未启动 → 不登记（批准后不误判 dedup 假死）
    g = GuardState()
    g.check("delete_bank", {"bank_id": 5})
    checks.append(("d11_blocked_not_registered", not g.is_duplicate("delete_bank", {"bank_id": 5})))
    # (b) 执行开始后登记 → 重试被去重跳过（防重放写）
    g.register_execution("delete_bank", {"bank_id": 5})
    r = g.check("delete_bank", {"bank_id": 5})
    checks.append(("d11_executed_blocks_retry", r.error_code == "dedup_skipped"))

    # 特殊字段剥离：_component/_route 剥离后 LLM 只见纯净结果
    clean, dirs = strip_special_fields({"_component": "x", "_route": "y", "a": 1})
    checks.append(("strip_special_fields", clean == {"a": 1} and dirs == {"component": "x", "route": "y"}))

    return checks


def evaluate_agent_tools() -> dict:
    """运行 Agent 工具组确定性检查，返回 {accuracy, evaluated, structural, structural_failures}。"""
    checks = _registry_checks() + _guard_checks()
    passed = sum(1 for _, ok in checks if ok)
    failures = [name for name, ok in checks if not ok]
    return {
        "accuracy": passed / len(checks) if checks else 0.0,
        "evaluated": len(checks),
        "structural": not failures,
        "structural_failures": failures,
    }
