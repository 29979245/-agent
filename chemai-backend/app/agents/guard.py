"""Guard 护栏层（doc 30 §5 / design D11）。

四层检查（每请求一个 GuardState 实例）：
  L1 前置条件   → missing_prerequisites     （必填参数校验）
  L2 调用限次   → limit_exceeded            （每工具每轮 call_limit）
  L3 去重       → dedup_skipped             （执行键在工具开始执行时登记，D11）
  L4 审批门控   → requires_approval_blocked （approval 工具未确认）

D11 登记时机：执行键 `(tool, sorted_args)` 在**审批通过后、工具开始执行时**登记——
  审批阻塞未启动 → 不登记（防批准后误判 dedup 假死）；
  副作用工具中途报错 → 执行键已存在 → 重试跳过（防重放写）。

特殊字段剥离：工具返回值中的 `_component` / `_route` 为前端专用，Guard 层剥离，
LLM 只见纯净业务结果（doc 30 §5.3）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

# doc 30 §5.2 各工具前置条件（可按需扩展；关键字匹配）
PREREQUISITES: dict[str, Callable[[dict], str | None]] = {
    "search_exam_bank": lambda a: (
        None if len(str(a.get("keyword") or "")) > 2 else "keyword 需大于 2 个字符"
    ),
    "diagnose_barrier": lambda a: (
        None if (a.get("student_id") or a.get("class_id") or a.get("student_name") or a.get("class_name"))
        else "student_id / class_id / 姓名 至少一个非空"
    ),
    "weekly_report": lambda a: (
        None if (a.get("student_id") or a.get("student_name") or a.get("class_name") or a.get("class_id"))
        else "至少一个学生/班级标识非空"
    ),
    "assign_adaptive_practice": lambda a: (
        None if (a.get("class_id") or a.get("class_name"))
        else "至少一个班级标识非空"
    ),
    "query_ocr_progress": lambda a: (
        None if a.get("batch_id") else "batch_id 必填"
    ),
    "grade_answer_sheets": lambda a: (
        None if a.get("batch_id") else "batch_id 必填"
    ),
    "save_grading_results": lambda a: (
        None if a.get("batch_id") else "batch_id 必填"
    ),
    "memory_student_get": lambda a: (
        None if a.get("student_id") else "student_id 必填"
    ),
    "generate_parent_report": lambda a: (
        None if a.get("student_id") else "student_id 必填"
    ),
    "send_report_to_parent": lambda a: (
        None if a.get("student_id") else "student_id 必填"
    ),
}

# 审批门控工具（doc 30 §5.2 第 4 层 / design D5）
# assign_adaptive_practice 已改为 preview-only，审批移至 API 确认端点（design D2）
APPROVAL_TOOLS: frozenset[str] = frozenset({
    "delete_bank",
    "grade_answer_sheets",
    "save_grading_results",
    "send_report_to_parent",
})


@dataclass
class GuardResult:
    ok: bool
    error_code: str | None = None
    message: str = ""
    payload: dict[str, Any] = field(default_factory=dict)

    @property
    def error(self) -> dict:
        return {"error": self.error_code, "message": self.message, "_guard_error": True}


class GuardState:
    """请求级护栏。去重/限次/审批状态每请求独立。"""

    def __init__(
        self,
        approval_tools: frozenset[str] = APPROVAL_TOOLS,
        call_limits: dict[str, int] | None = None,
        prerequisites: dict[str, Callable[[dict], str | None]] | None = None,
    ) -> None:
        self.approval_tools = approval_tools
        self.call_limits = dict(call_limits or {})
        self.prerequisites = dict(prerequisites or PREREQUISITES)
        self._calls: dict[str, int] = {}
        self._execution_keys: set[tuple[str, str]] = set()  # D11
        self._approved: set[tuple[str, str]] = set()  # 已确认的审批执行键

    # ---------- L1 前置条件 ----------
    def check_prerequisites(self, tool_name: str, args: dict) -> GuardResult:
        fn = self.prerequisites.get(tool_name)
        if fn is None:
            return GuardResult(ok=True)
        problem = fn(args or {})
        if problem:
            return GuardResult(ok=False, error_code="missing_prerequisites", message=problem)
        return GuardResult(ok=True)

    # ---------- L2 调用限次 ----------
    def check_limit(self, tool_name: str) -> GuardResult:
        limit = self.call_limits.get(tool_name)
        if limit is None:
            return GuardResult(ok=True)
        count = self._calls.get(tool_name, 0)
        if count >= limit:
            return GuardResult(
                ok=False,
                error_code="limit_exceeded",
                message=f"{tool_name} 本轮调用已达上限（{limit} 次）",
            )
        return GuardResult(ok=True)

    def record_call(self, tool_name: str) -> None:
        self._calls[tool_name] = self._calls.get(tool_name, 0) + 1

    # ---------- L3 去重（D11） ----------
    @staticmethod
    def _execution_key(tool_name: str, args: dict) -> tuple[str, str]:
        sorted_args = ",".join(f"{k}={v}" for k, v in sorted((args or {}).items()))
        return (tool_name, sorted_args)

    def is_duplicate(self, tool_name: str, args: dict) -> bool:
        return self._execution_key(tool_name, args) in self._execution_keys

    def register_execution(self, tool_name: str, args: dict) -> None:
        """D11：审批通过后、工具开始执行时登记执行键。"""
        self._execution_keys.add(self._execution_key(tool_name, args))

    # ---------- L4 审批门控 ----------
    def is_approval_tool(self, tool_name: str) -> bool:
        return tool_name in self.approval_tools

    def mark_approved(self, tool_name: str, args: dict) -> None:
        """审批确认后登记已批准，供恢复执行时校验。"""
        self._approved.add(self._execution_key(tool_name, args))

    def check_approval(self, tool_name: str, args: dict) -> GuardResult:
        """第 4 层：非 approval 工具直接通过；approval 工具未获批返回 blocked。"""
        if not self.is_approval_tool(tool_name):
            return GuardResult(ok=True)
        if self._execution_key(tool_name, args) in self._approved:
            return GuardResult(ok=True)
        return GuardResult(
            ok=False,
            error_code="requires_approval_blocked",
            message=f"{tool_name} 需要审批确认后才能执行",
            payload={"requires_approval": True, "tool": tool_name, "args": args},
        )

    # ---------- 四层总检 ----------
    def check(self, tool_name: str, args: dict) -> GuardResult:
        result = self.check_prerequisites(tool_name, args)
        if not result.ok:
            return result
        result = self.check_limit(tool_name)
        if not result.ok:
            return result
        if self.is_duplicate(tool_name, args):
            return GuardResult(ok=False, error_code="dedup_skipped",
                               message="相同调用已执行过，跳过")
        result = self.check_approval(tool_name, args)
        return result


# ---------- 特殊字段剥离（doc 30 §5.3） ----------
def strip_special_fields(result: Any) -> tuple[Any, dict]:
    """从工具返回值剥离 _component/_route 前端指令。

    返回 (纯净业务结果, 前端指令字典 {component, route})。
    """
    if not isinstance(result, dict):
        return result, {}
    component = result.pop("_component", None)
    route = result.pop("_route", None)
    directives: dict[str, Any] = {}
    if component is not None:
        directives["component"] = component
    if route is not None:
        directives["route"] = route
    return result, directives
