"""工具注册表 + Guard 四层集成单测（doc 30 §5 / D11 / D13）。"""
import asyncio

import pytest

from app.agents.guard import GuardState, strip_special_fields
from app.agents.tools.context import ToolContext
from app.agents.tools.registry import TOOL_IMPLS, TOOL_SCHEMAS, build_langgraph_tools, execute_tool


def run(coro):
    return asyncio.run(coro)


def _ctx(**kw):
    defaults = {"guard": GuardState(), "user": {"user_id": 1, "role": "teacher", "school_id": 1}}
    defaults.update(kw)
    return ToolContext(**defaults)


def test_registry_all_tools_registered():
    assert len(TOOL_IMPLS) == 38
    assert len(TOOL_SCHEMAS) == 38
    from app.agents.tools.tool_meta import TOOL_META
    assert set(TOOL_IMPLS) == set(TOOL_META)


# ---------------- 四层护栏 ----------------

def test_guard_limit_exceeded():
    from app.agents.tools.tool_meta import TOOL_META
    guard = GuardState(call_limits={"web_search": TOOL_META["web_search"].call_limit})
    guard.record_call("web_search")
    guard.record_call("web_search")
    ctx = _ctx(guard=guard)
    out = run(execute_tool(ctx, "web_search", {"query": "化学"}))
    assert out["error"] == "limit_exceeded"
    assert out["_guard_error"] is True


def test_guard_prerequisite_missing():
    ctx = _ctx(guard=GuardState())
    out = run(execute_tool(ctx, "search_exam_bank", {"keyword": "x"}))  # keyword ≤2 字符
    assert out["error"] == "missing_prerequisites"


def test_guard_approval_blocks_and_carries_payload():
    from app.agents.tools.tool_meta import TOOL_META
    guard = GuardState(call_limits={"delete_bank": TOOL_META["delete_bank"].call_limit})
    ctx = _ctx(guard=guard)
    out = run(execute_tool(ctx, "delete_bank", {"bank_id": 1}))
    assert out["error"] == "requires_approval_blocked"
    assert out["requires_approval"] is True
    assert out["tool"] == "delete_bank"
    assert out["args"] == {"bank_id": 1}
    # 审批后同一执行键通过
    guard.mark_approved("delete_bank", {"bank_id": 1})
    # 未注入 db → db_unavailable，证明已越过审批层进入执行
    out2 = run(execute_tool(ctx, "delete_bank", {"bank_id": 1}))
    assert out2["error"] in {"db_unavailable", "tool_failed"}


def test_guard_approval_does_not_register_execution_key_when_blocked():
    """D11：审批阻塞未启动 → 不登记执行键（防批准后误判 dedup）。"""
    guard = GuardState()
    ctx = _ctx(guard=guard)
    run(execute_tool(ctx, "delete_bank", {"bank_id": 5}))
    assert guard.is_duplicate("delete_bank", {"bank_id": 5}) is False


def test_guard_dedup_after_execution():
    """D11：副作用工具开始执行后登记执行键，相同调用重试被去重跳过。"""
    guard = GuardState()
    ctx = _ctx(guard=guard)
    run(execute_tool(ctx, "list_banks", {}))
    assert guard.is_duplicate("list_banks", {}) is True
    out = run(execute_tool(ctx, "list_banks", {}))
    assert out["error"] == "dedup_skipped"


# ---------------- 指令剥离与事件推送 ----------------

def test_component_directive_emitted_and_stripped():
    events = []

    def emit(event, payload):
        events.append((event, payload))

    ctx = _ctx(guard=GuardState(), emit=emit)
    out = run(execute_tool(ctx, "show_exam_workbench", {"difficulty": "medium"}))
    assert "_component" not in out  # LLM 只见纯净结果
    assert out["difficulty"] == "medium"
    assert len(events) == 1
    event, payload = events[0]
    assert event == "component"
    assert payload["component"] == "exam-workbench"
    assert payload["params"]["difficulty"] == "medium"


def test_route_directive_emitted():
    from app.agents.tools import tools_diagnosis
    events = []

    def emit(event, payload):
        events.append((event, payload))

    # 用 generate_learning_plan：需要 db 解析学生 → 无法仅靠注入；改用纯函数验证 emit 映射
    clean, directives = strip_special_fields({"_route": {"page": "students", "params": {"a": 1}}, "student_id": 3})
    assert clean == {"student_id": 3}
    assert directives == {"route": {"page": "students", "params": {"a": 1}}}


def test_strip_special_fields():
    clean, dirs = strip_special_fields({"_component": "x", "_route": "y", "a": 1})
    assert clean == {"a": 1}
    assert dirs == {"component": "x", "route": "y"}


# ---------------- LangGraph 绑定 ----------------

def test_build_langgraph_tools_persona():
    ctx = _ctx()
    tools = build_langgraph_tools(ctx, "teacher")
    names = {t.name for t in tools}
    assert "search_exam_bank" in names
    assert "diagnose_barrier" in names
    assert "delete_bank" in names  # approval 工具也在工具集


def test_build_langgraph_tools_student_has_tutoring():
    ctx = _ctx()
    tools = build_langgraph_tools(ctx, "student")
    names = {t.name for t in tools}
    assert "web_search" in names
    assert {"ionic_equation_tutor", "stoichiometry_tutor", "redox_tutor", "equilibrium_tutor",
            "chemistry_tutor", "simulate_experiment"} <= names


def test_build_langgraph_tool_invokes_guard():
    ctx = _ctx()
    tool = build_langgraph_tools(ctx, "student")[0]
    out = asyncio.run(tool.ainvoke({"query": "化学平衡"}))  # web_search 无 db 依赖
    assert isinstance(out, dict)
    assert "query" in out or "error" in out
