"""ReAct v2 单 Agent 工厂 + 对话执行管线（doc 30 §2 / doc 38 §3 / design D12/D14）。

- build_chat_agent：LangGraph `create_react_agent` 包装，Persona 过滤工具集，递归上限 12。
- resolve_version：version 门——默认 "v2"；为 "v1" 时显式报错（端点映射 501）。
- thread_key / make_agent_config：D14 检查点键 `{subject}:{thread_id}` + recursion_limit。
- make_checkpointer：AsyncSqliteSaver（agent_checkpoint.db），`async with` 用法。
- run_agent_chat：对话闭环——读检查点历史 → 按 `thread_id` 从检查点恢复增量对话
  （历史由 LangGraph 状态持久化，首轮注入学生档案 System）→ 构建 ToolContext + 工具 →
  `astream_events` → `agent_events_to_sse`，产出 SSE 事件元组流。
- 审批挂起注册表：D13 暂停时记录待审批，供 Gateway 审批流（task #5）查询/恢复。
- 断流污染自愈：客户端在工具执行中断开会在检查点留下孤儿 tool_call（无 ToolMessage），
  下一轮 LangGraph 校验失败线程即报废；run_agent_chat 读历史后补合成 ToolMessage 自愈。

历史管理：`inputs` 只喂「本轮增量」（新用户消息；档案 System 仅首轮）。重喂整个历史
会造成双源注入——LangGraph 按 thread_id 恢复检查点后再 append，历史每轮翻倍，且被
拍平的 ToolMessage（丢 tool_call_id）会在重建时报 KeyError。
"""
from __future__ import annotations

import asyncio
import re
import sqlite3
from typing import Any, AsyncIterator, Optional

from langchain_core.messages import ToolMessage

from app.agents.guard import GuardState, build_guard_config
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


def _message_text(content) -> str:
    """提取 BaseMessage.content 的纯文本：str 原样；内容块列表只取 text 块。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") in ("text", None):
                    parts.append(block.get("text") or "")
            elif isinstance(block, str):
                parts.append(block)
        return "".join(parts)
    return ""


def serialize_history(messages: list) -> list[dict]:
    """检查点消息 → 纯文本回放（user/assistant 气泡）。

    跳过 SystemMessage/ToolMessage 与仅含 tool_calls 的空内容 AIMessage；
    兼顾 LangChain BaseMessage 对象与 dict 两种形态（context.py 同款兼容）。
    """
    out: list[dict] = []
    for m in messages:
        if isinstance(m, dict):
            mtype, content = m.get("type"), m.get("content")
        else:
            mtype, content = getattr(m, "type", None), getattr(m, "content", "")
        text = _message_text(content)
        if mtype == "human":
            out.append({"role": "user", "content": text})
        elif mtype == "ai" and text.strip():
            out.append({"role": "assistant", "content": text})
    return out


def _orphaned_tool_calls(messages: list) -> list[dict]:
    """断流污染检测：历史中「有 tool_call 但缺对应 ToolMessage」的孤儿调用。

    客户端在工具执行中断开（关页面/刷新/服务重启/断网）会在检查点留下孤儿
    tool_call——LangGraph 下一轮校验「每个 tool_call 必有 ToolMessage」直接抛错，
    该线程从此每一轮都失败（前端显示「对话出错」/「连接中断」）。返回需补
    ToolMessage 的调用列表，保持历史顺序。
    """
    calls: dict[str, dict] = {}
    completed: set[str] = set()
    for m in messages:
        if isinstance(m, dict):
            mtype = m.get("type")
            tool_calls = m.get("tool_calls")
            call_id = m.get("tool_call_id")
        else:
            mtype = getattr(m, "type", None)
            tool_calls = getattr(m, "tool_calls", None)
            call_id = getattr(m, "tool_call_id", None)
        if mtype == "ai" and tool_calls:
            for call in tool_calls:
                cid = call.get("id") if isinstance(call, dict) else getattr(call, "id", None)
                if cid:
                    calls[cid] = call
        elif mtype == "tool" and call_id:
            completed.add(call_id)
    return [call for cid, call in calls.items() if cid not in completed]


def thread_created_ms(thread_id: str) -> int | None:
    """解析前端线程 id `conv_<epoch_ms>_<rand>` 的创建时间；非该格式返回 None。"""
    m = re.match(r"conv_(\d+)_", thread_id)
    return int(m.group(1)) if m else None


def list_user_thread_keys(user_id: int, db_path: str | None = None) -> list[tuple[str, str]]:
    """枚举用户线程键：(thread_key, thread_id)，按最近 checkpoint 倒序。

    检查点键 = `{user_id}:{thread_id}`（D14）。checkpoint_id 为时间有序 UUID
    （实证随线程创建单调递增），max(checkpoint_id) 即最近写入，可直接排序。
    db_path 供测试注入临时库；默认取配置的 agent_checkpoint_db。
    """
    prefix = f"{user_id}:"
    con = sqlite3.connect(db_path or settings.agent_checkpoint_db)
    try:
        rows = con.execute(
            "SELECT thread_id, max(checkpoint_id) AS m FROM checkpoints "
            "WHERE thread_id LIKE ? GROUP BY thread_id ORDER BY m DESC",
            (prefix + "%",),
        ).fetchall()
    finally:
        con.close()
    return [(key, key[len(prefix):]) for key, _m in rows]


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
    stream_tool_args: bool = True,
    checkpointer_factory=None,
    resume: Optional[dict] = None,
) -> AsyncIterator[tuple[str, dict]]:
    """单轮对话闭环，产出 `(event_name, payload)` SSE 元组流。

    - 按 Persona 加载 system_prompt 与工具集（YAML ∩ TOOL_META）。
    - ToolContext 注入 DB/用户/Guard（call_limits 取自 TOOL_META）/记忆/LLM + emit 队列。
    - 读检查点历史 → 按 thread_id 恢复，inputs 只喂本轮增量 → astream_events → agent_events_to_sse。
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
    guard = GuardState(**build_guard_config(TOOL_META))  # L2：call_limit + Token Bucket + 并发上限（TOOL_META 可配）
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
        # 学生档案 System（§9.1）：首轮注入；存量线程历史已含档案则不重复注入。
        # 对修复前创建的旧线程（历史无档案，如家长绑定注入上线前的会话）补注入，
        # 使 LLM 能感知当前孩子，避免继续拿 `${student_context}` 占位符去查。
        profile_message = getattr(mem, "profile_system_message", None)
        profile = profile_message() if profile_message is not None else None
        has_profile = any(
            getattr(m, "type", None) == "system"
            and "学生档案" in _message_text(getattr(m, "content", ""))
            for m in history
        )
        inputs = {"messages": []}
        if profile is not None and (not history or not has_profile):
            inputs["messages"].append(profile)
        # 断流污染自愈：客户端在工具执行中断开会在检查点留下孤儿 tool_call（无对应
        # ToolMessage），LangGraph 下一轮校验失败 → 线程报废。为每个孤儿补一条合成
        # ToolMessage 使历史恢复合法，线程可继续对话（紧邻孤儿之后追加，inputs 即在末尾）。
        for call in _orphaned_tool_calls(history):
            inputs["messages"].append(ToolMessage(
                content='{"error": "interrupted", "message": "上一步执行被中断，请重新安排"}',
                tool_call_id=call.get("id"),
            ))
        inputs["messages"].append({"role": "user", "content": message})

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
