"""ReAct v2 单 Agent 工厂 + 对话执行管线（doc 30 §2 / doc 38 §3 / design D12/D14）。

- build_chat_agent：LangGraph `create_react_agent` 包装，Persona 过滤工具集，递归上限 12。
- resolve_version：version 门——默认 "v2"；为 "v1" 时显式报错（端点映射 501）。
- thread_key / make_agent_config：D14 检查点键 `{subject}:{thread_id}` + recursion_limit。
- make_checkpointer：AsyncSqliteSaver（agent_checkpoint.db），`async with` 用法。
- run_agent_chat：对话闭环——读检查点历史 → ContextManager 三层裁剪 → 构建 ToolContext + 工具 →
  `astream_events` → `agent_events_to_sse`，产出 SSE 事件元组流。
- 审批挂起注册表：D13 暂停时记录待审批，供 Gateway 审批流（task #5）查询/恢复。

裁剪写回：AsyncSqliteSaver 拒绝同步 `update_state`（主线程抛 InvalidStateError），
故不做检查点回写；裁剪在每次读历史时幂等执行，LLM 上下文始终有界。
"""
from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Optional

from app.agents.context import ContextManager
from app.agents.guard import GuardState
from app.agents.memory import MemorySystem
from app.agents.personas.loader import effective_skills, load_persona
from app.agents.sse_adapter import agent_events_to_sse
from app.agents.tools.context import ToolContext
from app.agents.tools.registry import build_langgraph_tools
from app.agents.tools.tool_meta import TOOL_META
from app.config import settings

RECURSION_LIMIT = 12
DEFAULT_VERSION = "v2"


class AgentVersionError(RuntimeError):
    """version 门：v1 已下线（端点映射 HTTP 501）。"""


def resolve_version(version: Optional[str]) -> str:
    """version 门：默认 "v2"；"v1" 返回明确错误，其余非法值同样拒绝。"""
    v = (version or DEFAULT_VERSION).strip().lower()
    if v == "v2":
        return v
    if v == "v1":
        raise AgentVersionError("v1 Agent 已下线，仅支持 v2（单 Agent ReAct）")
    raise AgentVersionError(f"未识别的 Agent version: {version}（仅支持 v2）")


def thread_key(subject: int | str, thread_id: str) -> str:
    """D14：检查点键 `{JWT subject}:{thread_id}`，按用户隔离会话。"""
    return f"{subject}:{thread_id}"


def make_agent_config(key: str) -> dict:
    """LangGraph 调用配置：thread_id（检查点恢复）+ recursion_limit（递归上限）。"""
    return {
        "configurable": {"thread_id": key},
        "recursion_limit": RECURSION_LIMIT,
    }


def build_chat_agent(model, tools: list, system_prompt: str = "", checkpointer=None, version: Optional[str] = None):
    """LangGraph create_react_agent 包装（doc 30 §2.3 / doc 38 §3）。

    system_prompt 作为 `prompt`（create_react_agent 的 state_modifier 等价入参）。
    checkpointer 传入 AsyncSqliteSaver 后自动做检查点持久化。
    """
    from langgraph.prebuilt import create_react_agent

    resolve_version(version)
    return create_react_agent(
        model=model,
        tools=list(tools),
        prompt=system_prompt or None,
        checkpointer=checkpointer,
        name="chemai_agent",
    )


def make_checkpointer():
    """AsyncSqliteSaver（agent_checkpoint.db）。返回 async 上下文管理器：
    `async with make_checkpointer() as saver:`。
    """
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    return AsyncSqliteSaver.from_conn_string(settings.agent_checkpoint_db)


async def read_history(saver, config: dict) -> list:
    """从检查点读取对话历史（LangChain 消息对象列表）；无检查点返回空。"""
    tup = await saver.aget_tuple(config)
    if tup is None:
        return []
    return tup.checkpoint["channel_values"].get("messages") or []


# ---------- 审批挂起注册表（D13，Gateway 审批流使用） ----------

_pending_approvals: dict[str, dict] = {}


def store_pending_approval(key: str, info: dict) -> None:
    """记录待审批：key = thread_key，info = {tool, args, thread_id}。"""
    _pending_approvals[key] = info


def get_pending_approval(key: str) -> Optional[dict]:
    return _pending_approvals.get(key)


def pop_pending_approval(key: str) -> Optional[dict]:
    return _pending_approvals.pop(key, None)


# ---------- 对话执行管线 ----------


async def run_agent_chat(
    *,
    persona: str,
    user: Any,
    thread_id: str,
    message: str,
    db=None,
    llm_client=None,
    memory: Optional[MemorySystem] = None,
    summarizer=None,
    stream_tool_args: bool = True,
    checkpointer_factory=None,
    resume: Optional[dict] = None,
) -> AsyncIterator[tuple[str, dict]]:
    """单轮对话闭环，产出 `(event_name, payload)` SSE 元组流。

    - 按 Persona 加载 system_prompt 与工具集（YAML ∩ TOOL_META）。
    - ToolContext 注入 DB/用户/Guard（call_limits 取自 TOOL_META）/记忆/LLM + emit 队列。
    - 读检查点历史 → ContextManager 裁剪 → astream_events → agent_events_to_sse。
    - 审批暂停（D13）：on_approval_pending 记录待审批，流停止不发 done。
    - resume：审批恢复（D13）——decision="approve" 时预标记 Guard 审批通过，
      使 LLM 复调同一工具时第 4 层门控放行；message 由恢复端点注入审批指令。
    - checkpointer_factory：返回 AsyncSqliteSaver 上下文管理器（测试注入 tmp 路径）。
    """
    from app.agents.factories.model_factory import FallbackChatModel, LLMClient
    from app.agents.tools.search_client import get_search_client

    llm = llm_client or LLMClient()
    model = FallbackChatModel(llm=llm)

    persona_obj = load_persona(persona)
    skills = effective_skills(persona)

    key = thread_key(user.user_id, thread_id)
    config = make_agent_config(key)

    emit_queue: asyncio.Queue = asyncio.Queue()
    guard = GuardState(call_limits={name: meta.call_limit for name, meta in TOOL_META.items()})
    if resume and resume.get("decision") == "approve":
        # D13 恢复：预标记审批通过，LLM 复调该工具时 L4 放行
        guard.mark_approved(resume.get("tool") or "", resume.get("args") or {})
    mem = memory or MemorySystem()
    ctx = ToolContext(
        db=db,
        user=user,
        guard=guard,
        memory=mem,
        llm=llm,
        search=get_search_client(),
        emit=lambda name, payload: emit_queue.put_nowait((name, payload)),
        persona=persona,
    )

    tools = build_langgraph_tools(ctx, persona, skills=skills)

    checkpointer = checkpointer_factory or make_checkpointer
    async with checkpointer() as saver:
        agent = build_chat_agent(
            model, tools, persona_obj.system_prompt, checkpointer=saver, version=DEFAULT_VERSION
        )

        history = await read_history(saver, config)
        cm = ContextManager(summarizer=summarizer)
        prepared = await cm.prepare(history, message)
        inputs = {"messages": prepared["messages"]}

        def on_approval_pending(tool: str, args: dict) -> None:
            store_pending_approval(key, {
                "tool": tool, "args": args, "thread_id": thread_id, "persona": persona,
            })

        async for frame in agent_events_to_sse(
            agent.astream_events(inputs, config=config, version="v2"),
            emit_queue=emit_queue,
            on_approval_pending=on_approval_pending,
            stream_tool_args=stream_tool_args,
        ):
            yield frame
