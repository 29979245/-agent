"""Agent 对话端点（doc 30 §13 / doc 41 / design D12/D14，挂载于 /api/agent）。

- POST /chat/langgraph/stream  Agent SSE 流：12 事件，逐帧 `event:/data:` 输出。
- version 门：body.version 为 "v1" 时 501（AgentVersionError → HTTP 501）。
- D14 所有权：thread_id 含分隔符 ":" 时 403（防构造他人会话键跨用户读取）。
- 认证由 AuthMiddleware 执行（/api/agent 已在白名单移除 → 未携带令牌 401）。
"""
from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.agents.agent import (
    AgentVersionError,
    pop_pending_approval,
    resolve_version,
    run_agent_chat,
    thread_key,
)
from app.agents.factories.model_factory import LLMClient
from app.agents.gateway import classify_intent, navigate_shortcut
from app.agents.personas.loader import PersonaLoadError
from app.core.exceptions import APIException
from app.core.permissions import UserContext
from app.core.ratelimit import agent_limiter
from app.db.session import get_db

logger = logging.getLogger(__name__)

agent_router = APIRouter()

# 角色 → 默认 Persona（前端未指定 persona 时的回退映射）
_ROLE_DEFAULT_PERSONA = {
    "teacher": "teacher",
    "student": "student",
    "parent": "parent",
}


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=8000, description="用户消息")
    persona: str = ""  # 留空则取 context.persona 或角色默认
    thread_id: str = "default"
    version: str = "v2"
    context: dict[str, Any] = Field(default_factory=dict)  # {user_id, role, persona}


def _frame(name: str, payload: dict) -> str:
    """格式化 SSE 帧：`event: <name>\\ndata: <json>\\n\\n`（前端 parseSSE 契约）。"""
    return f"event: {name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _default_persona(role: str) -> str:
    return _ROLE_DEFAULT_PERSONA.get(role, "tutor")


def _resolve_persona(role: str, requested: str) -> str:
    """Persona 绑定角色（防 Persona 提权）：仅允许角色默认 persona。

    角色 → persona 为确定性映射（teacher→teacher / student→student / parent→parent / 其余→tutor）。
    客户端显式指定 persona 时 SHALL 等于该角色的默认 persona，否则 403——杜绝跨角色工具集提权
    （如学生以 teacher persona 取得教师工具集，见安全审计 B1）。
    """
    default = _default_persona(role)
    if requested and requested != default:
        raise APIException(403, f"persona '{requested}' 与登录角色 '{role}' 不匹配", "PERSONA_FORBIDDEN",
                           suggestion=f"请使用角色 {role} 对应的 persona（{default}）")
    return default


async def _stream_events(
    user: UserContext, request: ChatRequest, db: Session, llm: LLMClient, persona: str
) -> AsyncIterator[str]:
    try:
        async for name, payload in run_agent_chat(
            persona=persona,
            user=user,
            thread_id=request.thread_id,
            message=request.message,
            db=db,
            llm_client=llm,
        ):
            yield _frame(name, payload)
    except PersonaLoadError as exc:
        logger.warning("[agent] Persona 加载失败: %s", exc)
        yield _frame("error", {"type": "error", "message": str(exc), "recoverable": False})
        yield _frame("done", {"type": "done"})
    except Exception as exc:  # noqa: BLE001 —— 流中异常 → error + done 收尾
        logger.exception("[agent] 对话流异常: %s", exc)
        yield _frame("error", {"type": "error", "message": str(exc), "recoverable": True})
        yield _frame("done", {"type": "done"})


async def _stream_navigate(page: str) -> AsyncIterator[str]:
    """navigate 快捷路径：把原始事件元组格式化为 SSE 帧（navigate → done → 流终止）。"""
    async for name, payload in navigate_shortcut(page):
        yield _frame(name, payload)


def _stream_headers() -> dict:
    return {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }


@agent_router.post("/chat/langgraph/stream")
async def chat_stream(
    request: Request, body: ChatRequest, db: Session = Depends(get_db)
) -> StreamingResponse:
    """Agent SSE 流：12 事件（phase/tool_call/tool_args/tool_result/text/.../done）。

    Gateway 分类（doc 30 六）：LLM 语义分类优先 + 关键词兜底；
    navigate 意图走快捷路径（跳过 ReAct），chat 进入 ReAct 循环。
    """
    # version 门（v1 → 501，明确错误）
    try:
        resolve_version(body.version)
    except AgentVersionError as exc:
        raise APIException(501, str(exc), "AGENT_VERSION_UNSUPPORTED",
                           suggestion="请使用 v2 Agent（单 Agent ReAct）") from exc

    # AuthMiddleware 写入原始 JWT payload（dict）；转换为 UserContext 供管线使用
    payload = request.state.user
    user = UserContext(
        user_id=payload["user_id"],
        role=payload["role"],
        school_id=payload.get("school_id"),
    )

    # 防 Persona 提权（安全审计 B1）：persona 绑定角色，客户端不得跨角色指定
    persona = _resolve_persona(user.role, body.persona or body.context.get("persona"))

    # D14 所有权：前端 context.user_id 与 JWT 主体不一致 → 403（防伪造他人会话上下文）
    ctx_uid = body.context.get("user_id")
    if ctx_uid is not None and ctx_uid != payload["user_id"]:
        raise APIException(403, "context.user_id 与登录用户不一致", "THREAD_OWNERSHIP_DENIED",
                           suggestion="请以登录态为准，勿在 context 中伪造他人用户")

    # D14 所有权：thread_id 不得包含分隔符 ":"（防止构造他人 `{subject}:{thread_id}` 键）
    if ":" in body.thread_id:
        raise APIException(403, "thread_id 含非法分隔符", "THREAD_OWNERSHIP_DENIED",
                           suggestion="请使用不含冒号的会话标识")

    # Token Bucket 限流（D6）：按用户
    if not agent_limiter.allow(str(user.user_id)):
        raise APIException(429, "请求过于频繁，请稍后再试", "RATE_LIMIT_EXCEEDED",
                           suggestion="请稍后重试")

    # Gateway 意图分类：共享一个 LLMClient（Gateway 分类 + ReAct 执行）
    llm = LLMClient()
    try:
        intent = await classify_intent(body.message, llm=llm)
    except Exception:  # noqa: BLE001 —— 分类失败不阻塞对话，按 chat 处理
        logger.exception("[agent] Gateway 分类异常，按 chat 处理")
        intent = type("_Intent", (), {"type": "chat", "page": None})()

    if intent.type == "navigate" and intent.page:
        return StreamingResponse(_stream_navigate(intent.page), media_type="text/event-stream",
                                 headers=_stream_headers())

    return StreamingResponse(
        _stream_events(user, body, db, llm, persona),
        media_type="text/event-stream",
        headers=_stream_headers(),
    )


# ---------------------------------------------------------------- 审批恢复（D13）


class ApprovalResumeRequest(BaseModel):
    thread_id: str = "default"
    decision: str = "approve"  # "approve" | "reject"
    tool: str = ""             # 仅展示用；恢复以注册表为准
    args: dict[str, Any] = Field(default_factory=dict)


def _approval_instruction(pending: dict, decision: str) -> str:
    """把审批结果注入为新用户消息：approve 指示 LLM 复调该工具，reject 指示调整行为。"""
    tool = pending.get("tool") or ""
    args = pending.get("args") or {}
    if decision == "approve":
        return f"用户已确认执行工具 {tool}，参数：{args}。请立即调用该工具完成操作。"
    return f"用户已拒绝执行工具 {tool}。请停止该操作，改用其他方式继续对话或向用户说明。"


async def _stream_resume(
    user: UserContext, pending: dict, decision: str, db: Session, llm: LLMClient
) -> AsyncIterator[str]:
    persona = _default_persona(user.role)  # 防提权：恢复路径 persona 一律按角色推导，不信任 pending 存储值
    message = _approval_instruction(pending, decision)
    resume = {"tool": pending.get("tool"), "args": pending.get("args"), "decision": decision}
    try:
        async for name, payload in run_agent_chat(
            persona=persona,
            user=user,
            thread_id=pending.get("thread_id", "default"),
            message=message,
            db=db,
            llm_client=llm,
            resume=resume,
        ):
            yield _frame(name, payload)
    except Exception as exc:  # noqa: BLE001 —— 恢复流异常 → error + done 收尾
        logger.exception("[agent] 审批恢复流异常: %s", exc)
        yield _frame("error", {"type": "error", "message": str(exc), "recoverable": True})
        yield _frame("done", {"type": "done"})


@agent_router.post("/approval/resume")
async def approval_resume(
    request: Request, body: ApprovalResumeRequest, db: Session = Depends(get_db)
) -> StreamingResponse:
    """审批恢复（D13）：教师确认/取消后从检查点恢复，重开新 SSE 流续推。"""
    if body.decision not in ("approve", "reject"):
        raise APIException(422, "decision 仅支持 approve / reject", "VALIDATION_ERROR",
                           suggestion="请传入 approve 或 reject")

    payload = request.state.user
    user = UserContext(
        user_id=payload["user_id"],
        role=payload["role"],
        school_id=payload.get("school_id"),
    )

    # D14 所有权：恢复同样按 `{JWT 主体}:{thread_id}` 键查询，他人无法恢复
    if ":" in body.thread_id:
        raise APIException(403, "thread_id 含非法分隔符", "THREAD_OWNERSHIP_DENIED",
                           suggestion="请使用不含冒号的会话标识")

    key = thread_key(user.user_id, body.thread_id)
    pending = pop_pending_approval(key)
    if pending is None:
        raise APIException(409, "该会话无待审批的请求", "NO_PENDING_APPROVAL",
                           suggestion="请先让 Agent 发起审批工具调用")

    return StreamingResponse(
        _stream_resume(user, pending, body.decision, db, LLMClient()),
        media_type="text/event-stream",
        headers=_stream_headers(),
    )
