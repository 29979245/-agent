"""Agent 对话端点（doc 30 §13 / doc 41 / design D12/D14，挂载于 /api/agent）。

- POST /chat/langgraph/stream  Agent SSE 流：12 事件，逐帧 `event:/data:` 输出。
- version 门：body.version 为 "v1" 时 501（AgentVersionError → HTTP 501）。
- D14 所有权：thread_id 含分隔符 ":" 时 403（防构造他人会话键跨用户读取）。
- 认证由 AuthMiddleware 执行（/api/agent 已在白名单移除 → 未携带令牌 401）。
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncIterator

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.agents.agent import (
    AgentVersionError,
    _message_text,
    list_user_thread_keys,
    make_agent_config,
    make_checkpointer,
    pop_pending_approval,
    read_history,
    resolve_version,
    run_agent_chat,
    serialize_history,
    thread_created_ms,
    thread_key,
)
from app.agents.factories.model_factory import LLMClient
from app.agents.gateway import classify_intent, navigate_shortcut
from app.agents.memory import MemorySystem
from app.agents.personas.loader import PersonaLoadError
from app.agents.sse_adapter import HEARTBEAT
from app.core.exceptions import APIException
from app.core.permissions import UserContext
from app.core.ratelimit import agent_limiter
from app.db.models import Account, Student, StudentParentBinding
from app.db.models.enums import AccountRole
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
    """格式化 SSE 帧：`event: <name>\\ndata: <json>\\n\\n`（前端 parseSSE 契约）。

    心跳帧（HEARTBEAT）渲染为 SSE 注释 `: ping\\n\\n`——前端 parseSSE 无 event/data
    行直接忽略，仅在网络层保活，防止长静默被空闲超时掐断。
    """
    if name == HEARTBEAT:
        return ": ping\n\n"
    return f"event: {name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _default_persona(role: str) -> str:
    return _ROLE_DEFAULT_PERSONA.get(role, "tutor")


def _resolve_parent_child(db: Session, user_id: int, child_id) -> int | None:
    """家长对话目标孩子校验：child_id 为该家长 active 绑定子女时返回之，否则 None。

    身份链：user_id = Account.id → Account.role_id = Parent.id → 绑定表。
    context.student_id 来自客户端，必须校验绑定关系防越权注入他人孩子档案。
    """
    account = db.get(Account, user_id)
    if account is None or account.role != AccountRole.parent:
        return None
    binding = (
        db.query(StudentParentBinding)
        .filter(
            StudentParentBinding.parent_id == account.role_id,
            StudentParentBinding.student_id == child_id,
            StudentParentBinding.status == "active",
        )
        .first()
    )
    return child_id if binding is not None else None


def _bind_child_profile(memory: MemorySystem, student: Student) -> None:
    """注入学生档案 System Message（§9.1，供模型知晓「我在聊哪个孩子」）。"""
    memory.bind_student_profile({
        "student_id": student.id,
        "name": student.name,
        "class_id": student.class_id,
        "class_name": student.class_.name if student.class_ else "",
    })


def _build_memory(db: Session, user: UserContext, context: dict | None = None) -> MemorySystem:
    """构造三层记忆；学生角色绑定本人档案，家长角色绑定 context 当前子女档案
    （身份注入 §9.1，供模型知晓「我是谁 / 我在聊哪个孩子」）。

    - student：身份链 user_id = Account.id → Account.role_id = Student.id。
    - parent：context.student_id 须为 active 绑定子女（越权不注入，端点层另行 403）；
      未显式指定但仅绑定 1 个 active 子女时自动注入，避免 LLM 在无档案时
      拿模板占位符（如 `${student_context}`）去查学生。
    """
    memory = MemorySystem()
    if user.role == "student":
        account = db.get(Account, user.user_id)
        student = db.get(Student, account.role_id) if account else None
        if student is not None:
            _bind_child_profile(memory, student)
    elif user.role == "parent":
        sid = (context or {}).get("student_id")
        if sid is not None and _resolve_parent_child(db, user.user_id, sid) == sid:
            student = db.get(Student, sid)
            if student is not None:
                _bind_child_profile(memory, student)
        elif sid is None:
            account = db.get(Account, user.user_id)
            if account is not None:
                child_ids = [
                    row[0] for row in db.query(StudentParentBinding.student_id).filter(
                        StudentParentBinding.parent_id == account.role_id,
                        StudentParentBinding.status == "active",
                    ).all()
                ]
                if len(child_ids) == 1:
                    student = db.get(Student, child_ids[0])
                    if student is not None:
                        _bind_child_profile(memory, student)
    return memory


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
            memory=_build_memory(db, user, request.context),
        ):
            yield _frame(name, payload)
    except PersonaLoadError as exc:
        logger.warning("[agent] Persona 加载失败: %s", exc)
        yield _frame("error", {"type": "error", "message": str(exc), "recoverable": False})
        yield _frame("done", {"type": "done"})
    except asyncio.CancelledError:
        # 客户端断开 → 后端流被取消。日志留痕：若检查点恰在工具执行中断开，
        # 会留下孤儿 tool_call（agent.py 已做自愈），此处仅确认断连来源。
        logger.info("[agent] 对话流被客户端断开（SSE 取消），thread=%s", request.thread_id)
        raise
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

    # 家长绑定校验：context.student_id 必须为当前家长 active 绑定子女（防越权聊他人孩子）
    if user.role == "parent":
        sid = body.context.get("student_id")
        if sid is not None and _resolve_parent_child(db, user.user_id, sid) != sid:
            raise APIException(403, "无权查看该学生的学情", "PARENT_CHILD_NOT_BOUND",
                               suggestion="请在家长端选择已绑定的子女")

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
            memory=_build_memory(db, user),
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


# ---------------------------------------------------------------- 会话历史（设计：对话跨 tab 持久化）


@agent_router.get("/threads")
async def list_threads(request: Request, db: Session = Depends(get_db)) -> dict:
    """当前用户的会话列表（学生端历史抽屉）：按最近 checkpoint 倒序，标题=首条用户消息。

    键 = `{user_id}:{thread_id}`（D14），仅枚举当前 JWT 主体的线程，天然防跨用户读取。
    """
    payload = request.state.user
    user_id = payload["user_id"]
    items: list[dict] = []
    async with make_checkpointer() as saver:
        for key, tid in list_user_thread_keys(user_id):
            tup = await saver.aget_tuple(make_agent_config(key))
            if tup is None:
                continue
            messages = tup.checkpoint["channel_values"].get("messages") or []
            title = ""
            for m in messages:
                if getattr(m, "type", None) == "human" and _message_text(getattr(m, "content", "")).strip():
                    title = _message_text(getattr(m, "content", ""))[:24]
                    break
            items.append({
                "thread_id": tid,
                "title": title or "新对话",
                "created_ms": thread_created_ms(tid),
                "message_count": len(messages),
            })
    return {"threads": items}


@agent_router.get("/threads/{thread_id}/messages")
async def thread_messages(request: Request, thread_id: str, db: Session = Depends(get_db)) -> dict:
    """单线程消息回放（纯文本 user/assistant 气泡）。D14 键隔离：`:` 入参 403。"""
    payload = request.state.user
    user_id = payload["user_id"]
    if ":" in thread_id:
        raise APIException(403, "thread_id 含非法分隔符", "THREAD_OWNERSHIP_DENIED",
                           suggestion="请使用不含冒号的会话标识")
    key = thread_key(user_id, thread_id)
    async with make_checkpointer() as saver:
        messages = await read_history(saver, make_agent_config(key))
    return {"thread_id": thread_id, "messages": serialize_history(messages)}
