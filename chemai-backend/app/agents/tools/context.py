"""工具执行上下文（doc 30 §3）：每请求注入 DB / 用户 / 护栏 / 记忆 / 检索 / LLM 依赖。

工具实现只依赖 ToolContext 拿到所需资源，不直接碰全局单例，便于测试注入与按请求隔离。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from sqlalchemy.orm import Session

from app.agents.guard import GuardState


@dataclass
class ToolContext:
    db: Optional[Session] = None
    user: Any = None  # UserContext（含 user_id/role/school_id）
    guard: Optional[GuardState] = None
    request: Any = None  # FastAPI Request（如需要）
    memory: Any = None  # MemorySystem（三层记忆）
    bank: Any = None  # HistoricalBank（真题库单例）
    llm: Any = None  # LLMClient（三级回退）
    search: Any = None  # SearchClient（独立联网搜索）
    emit: Optional[Callable[[str, dict], None]] = None  # SSE 事件推送回调
    persona: str = ""  # 当前对话 Persona（审计日志）

    @property
    def safe_guard(self) -> GuardState:
        """无 guard 时提供空护栏（不拦、不限次、无审批），便于纯业务直调。"""
        if self.guard is None:
            self.guard = GuardState()
        return self.guard


# ---------- 身份辅助（各工具组共用，勿在各工具模块重复定义） ----------

def user_role(ctx: ToolContext) -> str:
    """当前登录角色（user_id/role/school_id；dict 或对象兼容）。"""
    user = ctx.user
    if user is None:
        return ""
    if isinstance(user, dict):
        return str(user.get("role") or "")
    return str(getattr(user, "role", "") or "")


def user_id(ctx: ToolContext) -> int:
    """当前登录账户 ID（Account.id；JWT payload['user_id']）。"""
    user = ctx.user
    if user is None:
        return 0
    if isinstance(user, dict):
        return int(user.get("user_id") or 0)
    return int(getattr(user, "user_id", 0) or 0)


def user_school_id(ctx: ToolContext) -> int | None:
    """当前登录学校 ID（可能为 None）。"""
    user = ctx.user
    if user is None:
        return None
    if isinstance(user, dict):
        return user.get("school_id")
    return getattr(user, "school_id", None)
