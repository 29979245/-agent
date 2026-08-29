"""ReAct v2 Agent 工厂 + 对话执行管线单测（tasks 对话闭环）。

覆盖：
- version 门：默认 v2，v1/非法值 → AgentVersionError（端点映射 501）。
- D14 检查点键：thread_key = `{subject}:{thread_id}`；config 带 recursion_limit 12。
- build_chat_agent：create_react_agent 包装 + 系统提示词；v1 抛错。
- 审批挂起注册表：store/get/pop。
- run_agent_chat 闭环：读检查点 → 裁剪 → 工具构建 → ReAct → SSE 适配器。
  - 纯文本回复：phase(text) → done。
  - 工具往返：tool_call → tool_args → executing → tool_result → text → done。
"""
import asyncio
import os

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, ToolCallChunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult

from app.agents.agent import (
    DEFAULT_VERSION,
    RECURSION_LIMIT,
    AgentVersionError,
    build_chat_agent,
    get_pending_approval,
    make_agent_config,
    pop_pending_approval,
    resolve_version,
    run_agent_chat,
    store_pending_approval,
    thread_key,
)
from app.agents.factories.model_factory import LLMClient
from app.core.permissions import UserContext


# ---------------------------------------------------------------- 假模型

class FakeModel(BaseChatModel):
    """固定文本回复的假模型（同时支持 ainvoke/astream）。"""

    def __init__(self, reply="ok"):
        super().__init__()
        self._reply = reply

    @property
    def _llm_type(self):
        return "fake"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=self._reply))])

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        # langchain-core 1.6：_astream 须产出 ChatGenerationChunk（含 .message）
        yield ChatGenerationChunk(message=AIMessageChunk(content=self._reply))

    def bind_tools(self, tools, **kwargs):
        return self


class ScriptedToolModel(BaseChatModel):
    """脚本化假模型：第 1 次调用返回 web_search 工具调用，之后返回文本。"""

    calls: int = 0  # pydantic 字段（BaseChatModel 禁止 setattr 非字段属性）

    @property
    def _llm_type(self):
        return "fake"

    def _next(self):
        self.calls += 1
        if self.calls == 1:
            return AIMessage(content="", tool_calls=[
                {"name": "web_search", "args": {"query": "化学"}, "id": "c1", "type": "tool_call"}
            ])
        return AIMessage(content="已联网查询")

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        return ChatResult(generations=[ChatGeneration(message=self._next())])

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        msg = self._next()
        if msg.tool_calls:
            yield ChatGenerationChunk(message=AIMessageChunk(content="", tool_call_chunks=[
                ToolCallChunk(index=0, name="web_search", args='{"query": "化学"}', id="c1")
            ]))
        else:
            yield ChatGenerationChunk(message=AIMessageChunk(content="已联网查询"))

    def bind_tools(self, tools, **kwargs):
        return self


def _client(model) -> LLMClient:
    return LLMClient(model_builder=lambda provider: model)


def _cp(tmp_path):
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    return AsyncSqliteSaver.from_conn_string(str(tmp_path / "cp.db"))


# ---------------------------------------------------------------- version 门

def test_resolve_version_default_v2():
    assert resolve_version(None) == "v2"
    assert resolve_version("v2") == "v2"
    assert resolve_version("") == "v2"


def test_resolve_version_v1_raises():
    with pytest.raises(AgentVersionError):
        resolve_version("v1")


def test_resolve_version_unknown_raises():
    with pytest.raises(AgentVersionError):
        resolve_version("v3")


# ---------------------------------------------------------------- D14 检查点键

def test_thread_key_subject_and_thread():
    assert thread_key(5, "abc") == "5:abc"
    assert thread_key("2024001", "t1") == "2024001:t1"


def test_make_agent_config_recursion_limit():
    cfg = make_agent_config("5:abc")
    assert cfg["configurable"]["thread_id"] == "5:abc"
    assert cfg["recursion_limit"] == RECURSION_LIMIT


# ---------------------------------------------------------------- build_chat_agent

def test_build_chat_agent_v2_returns_compiled():
    agent = build_chat_agent(FakeModel(), [], "你是化学老师", version="v2")
    assert hasattr(agent, "ainvoke")
    assert hasattr(agent, "astream_events")


def test_build_chat_agent_v1_raises():
    with pytest.raises(AgentVersionError):
        build_chat_agent(FakeModel(), [], "sys", version="v1")


# ---------------------------------------------------------------- 审批挂起注册表

def test_pending_approval_registry_roundtrip():
    key = thread_key(5, "t1")
    assert get_pending_approval(key) is None
    store_pending_approval(key, {"tool": "assign_adaptive_practice", "args": {"class_id": 1}, "thread_id": "t1"})
    info = get_pending_approval(key)
    assert info["tool"] == "assign_adaptive_practice"
    assert pop_pending_approval(key)["thread_id"] == "t1"
    assert get_pending_approval(key) is None
    assert pop_pending_approval("missing") is None


# ---------------------------------------------------------------- run_agent_chat 闭环

def test_run_agent_chat_text_reply(tmp_path):
    async def _run():
        frames = []
        async for f in run_agent_chat(
            persona="student",
            user=UserContext(user_id=1, role="student"),
            thread_id="t1",
            message="你好",
            llm_client=_client(FakeModel(reply="你好，我是化学助手")),
            checkpointer_factory=lambda: _cp(tmp_path),
        ):
            frames.append(f)
        return frames

    frames = asyncio.run(_run())
    names = [n for n, _ in frames]
    assert names[-1] == "done"
    texts = [p["content"] for n, p in frames if n == "text"]
    assert "你好，我是化学助手" in texts
    # 无工具调用 → 无 tool_call 事件
    assert "tool_call" not in names


def test_run_agent_chat_tool_roundtrip(tmp_path):
    async def _run():
        frames = []
        async for f in run_agent_chat(
            persona="student",
            user=UserContext(user_id=2, role="student"),
            thread_id="t2",
            message="帮我查一下化学知识点",
            llm_client=_client(ScriptedToolModel()),
            checkpointer_factory=lambda: _cp(tmp_path),
        ):
            frames.append(f)
        return frames

    frames = asyncio.run(_run())
    names = [n for n, _ in frames]
    # tool_call(preparing) → tool_args → executing → tool_result → reply(text) → done
    assert names == ["tool_call", "tool_args", "phase", "tool_result", "phase", "text", "done"]
    # tool_result：web_search 未配置 → 优雅降级摘要
    tr = next(p for n, p in frames if n == "tool_result")
    assert tr["success"] is True
    assert tr["tool"] == "web_search"
    assert "联网搜索服务未配置" in tr["result"]["summary"]
    # 最终文本
    texts = [p["content"] for n, p in frames if n == "text"]
    assert texts[-1] == "已联网查询"


def test_run_agent_chat_persona_toolset_bounded(tmp_path):
    """student Persona 仅 web_search：模型无权调用诊断/出题工具。"""
    calls = {}

    class TrapModel(BaseChatModel):
        @property
        def _llm_type(self):
            return "fake"

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="ok"))])

        async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
            yield AIMessageChunk(content="ok")

        def bind_tools(self, tools, **kwargs):
            calls["tools"] = [getattr(t, "name", str(t)) for t in tools]
            return self

    async def _run():
        frames = []
        async for f in run_agent_chat(
            persona="student",
            user=UserContext(user_id=3, role="student"),
            thread_id="t3",
            message="hi",
            llm_client=_client(TrapModel()),
            checkpointer_factory=lambda: _cp(tmp_path),
        ):
            frames.append(f)
        return frames

    asyncio.run(_run())
    bound = calls["tools"]
    assert bound == ["web_search"]
    assert "diagnose_barrier" not in bound
    assert "generate_questions" not in bound
