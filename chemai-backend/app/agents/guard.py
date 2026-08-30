"""Guard 护栏层（doc 30 §5 / design D11 / 课程验收点强化）。

四层检查（每请求一个 GuardState 实例）：
  L1 前置条件   → missing_prerequisites     （必填参数 + 会话/认证上下文校验）
  L2 调用限次   → limit_exceeded            （每工具每轮 call_limit + Token Bucket + 并发在途上限）
  L3 去重       → dedup_skipped             （执行键在工具开始执行时登记，带超时窗口，D11）
  L4 审批门控   → requires_approval_blocked （approval 工具未确认）

D11 登记时机：执行键 `(tool, sorted_args)` 在**审批通过后、工具开始执行时**登记——
  审批阻塞未启动 → 不登记（防批准后误判 dedup 假死）；
  副作用工具中途报错 → 执行键已存在 → 重试跳过（防重放写）。
  执行键带 `dedup_window_s` 超时窗口，窗口过期后相同调用可再次执行。

特殊字段剥离：工具返回值中的 `_component` / `_route` 为前端专用，Guard 层剥离，
LLM 只见纯净业务结果（doc 30 §5.3）。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

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


@dataclass
class TokenBucket:
    """令牌桶（L2）：每秒补充 rate 令牌，容量 capacity，令牌耗尽拒绝放行。

    tokens=None 表示首次 refill 时以满桶起步（初始突发 = capacity，随后按 rate 补充）。
    """

    rate: float
    capacity: int
    tokens: Optional[float] = None
    last_refill: float = field(default_factory=time.monotonic)

    def refill(self, now: float) -> None:
        if self.tokens is None:  # 首次使用：满桶起步
            self.tokens = float(self.capacity)
            self.last_refill = now
            return
        self.tokens = min(self.capacity, self.tokens + (now - self.last_refill) * self.rate)
        self.last_refill = now

    def consume(self) -> bool:
        """消耗 1 枚令牌；不足则返回 False 且不扣减。"""
        self.refill(time.monotonic())
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False


class GuardState:
    """请求级护栏。去重/限次/审批状态每请求独立。"""

    def __init__(
        self,
        approval_tools: frozenset[str] = APPROVAL_TOOLS,
        call_limits: dict[str, int] | None = None,
        prerequisites: dict[str, Callable[[dict], str | None]] | None = None,
        token_rates: dict[str, float] | None = None,
        token_capacity: dict[str, int] | None = None,
        max_concurrent: int | dict[str, int] = 1,
        dedup_window_s: float = 60.0,
    ) -> None:
        self.approval_tools = approval_tools
        self.call_limits = dict(call_limits or {})
        self.prerequisites = dict(prerequisites or PREREQUISITES)
        self.token_rates = dict(token_rates or {})  # 工具 → 每秒补充令牌数
        self.token_capacity = dict(token_capacity or {})  # 工具 → 桶容量（缺省 rate*2）
        self.max_concurrent = max_concurrent  # 并发在途上限：全局或按工具
        self.dedup_window_s = dedup_window_s  # L3 去重超时窗口（秒）
        self._calls: dict[str, int] = {}
        self._buckets: dict[str, TokenBucket] = {}
        self._in_flight: dict[str, int] = {}
        self._execution_keys: dict[tuple[str, str], float] = {}  # D11 执行键 → 登记时刻
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

    def check_context(self, ctx: Any) -> GuardResult:
        """L1 会话/认证上下文校验：ToolContext 缺 user 身份或角色返回 missing_prerequisites。

        身份 = user_id 非空，角色 = role 非空；两者缺一即拦截（规格「缺 user 身份/角色」）。
        无上下文（None）或直调场景不拦截；校验逻辑内联避免与 tools/context 循环依赖。
        """
        if ctx is None:
            return GuardResult(ok=True)
        user = getattr(ctx, "user", None)
        role = ""
        user_id = 0
        if user is not None:
            if isinstance(user, dict):
                role = str(user.get("role") or "")
                user_id = int(user.get("user_id") or 0)
            else:
                role = str(getattr(user, "role", "") or "")
                user_id = int(getattr(user, "user_id", 0) or 0)
        if not role or not user_id:
            return GuardResult(
                ok=False,
                error_code="missing_prerequisites",
                message="缺少用户身份/角色上下文，无法执行工具",
            )
        return GuardResult(ok=True)

    # ---------- L2 调用限次 ----------
    def _bucket(self, tool_name: str) -> Optional[TokenBucket]:
        if tool_name not in self.token_rates:
            return None
        if tool_name not in self._buckets:
            rate = self.token_rates[tool_name]
            cap = self.token_capacity.get(tool_name, int(rate * 2))
            self._buckets[tool_name] = TokenBucket(rate=rate, capacity=cap)  # 首用满桶
        return self._buckets[tool_name]

    def _concurrency_limit(self, tool_name: str) -> int:
        if isinstance(self.max_concurrent, dict):
            return self.max_concurrent.get(tool_name, 1)
        return self.max_concurrent

    def check_limit(self, tool_name: str) -> GuardResult:
        limit = self.call_limits.get(tool_name)
        count = self._calls.get(tool_name, 0)
        if limit is not None and count >= limit:
            return GuardResult(
                ok=False,
                error_code="limit_exceeded",
                message=f"{tool_name} 本轮调用已达上限（{limit} 次）",
            )
        bucket = self._bucket(tool_name)
        if bucket is not None:
            bucket.refill(time.monotonic())
            if bucket.tokens < 1.0:
                return GuardResult(ok=False, error_code="limit_exceeded",
                                   message=f"{tool_name} 令牌耗尽，请稍后再试")
        concurrency = self._concurrency_limit(tool_name)
        if self._in_flight.get(tool_name, 0) >= concurrency:
            return GuardResult(ok=False, error_code="limit_exceeded",
                               message=f"{tool_name} 并发在途已达上限（{concurrency}）")
        return GuardResult(ok=True)

    def record_call(self, tool_name: str) -> None:
        """记录本轮调用次数并扣减令牌桶（仅校验通过后调用）。"""
        self._calls[tool_name] = self._calls.get(tool_name, 0) + 1
        bucket = self._bucket(tool_name)
        if bucket is not None:
            bucket.consume()

    def begin_execution(self, tool_name: str) -> None:
        """执行开始：并发在途数 +1（registry.execute_tool 挂钩）。"""
        self._in_flight[tool_name] = self._in_flight.get(tool_name, 0) + 1

    def end_execution(self, tool_name: str) -> None:
        """执行结束（含异常）：并发在途数 -1（registry.execute_tool 挂钩）。"""
        self._in_flight[tool_name] = max(0, self._in_flight.get(tool_name, 0) - 1)

    # ---------- L3 去重（D11） ----------
    @staticmethod
    def _execution_key(tool_name: str, args: dict) -> tuple[str, str]:
        sorted_args = ",".join(f"{k}={v}" for k, v in sorted((args or {}).items()))
        return (tool_name, sorted_args)

    def is_duplicate(self, tool_name: str, args: dict) -> bool:
        key = self._execution_key(tool_name, args)
        ts = self._execution_keys.get(key)
        if ts is None:
            return False
        return (time.monotonic() - ts) < self.dedup_window_s  # 窗口过期后可重试

    def register_execution(self, tool_name: str, args: dict) -> None:
        """D11：审批通过后、工具开始执行时登记执行键与登记时刻。"""
        self._execution_keys[self._execution_key(tool_name, args)] = time.monotonic()

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
    def check(self, tool_name: str, args: dict, ctx: Any = None) -> GuardResult:
        result = self.check_prerequisites(tool_name, args)
        if not result.ok:
            return result
        result = self.check_context(ctx)
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


def build_guard_config(meta: dict[str, Any]) -> dict:
    """由 TOOL_META 生成 GuardState 构造参数（L2：Token Bucket + 并发上限按工具配置）。

    仅收纳显式声明的 token_rate / token_capacity / max_concurrent；未声明的工具
    沿用 GuardState 默认（令牌桶不启用、并发上限 1），与 design.md「默认 1，TOOL_META 可配置」一致。
    """
    return {
        "call_limits": {n: m.call_limit for n, m in meta.items()},
        "token_rates": {n: m.token_rate for n, m in meta.items() if m.token_rate is not None},
        "token_capacity": {n: m.token_capacity for n, m in meta.items() if m.token_capacity is not None},
        "max_concurrent": {n: m.max_concurrent for n, m in meta.items() if m.max_concurrent is not None},
    }


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
