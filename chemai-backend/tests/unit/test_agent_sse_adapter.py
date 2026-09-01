"""SSE 适配器单测（doc 30 §13.1 / doc 41 / design D12/D13，tasks 对话闭环）。

覆盖 12 事件映射：
- 非流式 Provider（D12）：on_chat_model_end 单次 tool_call（完整参数内联）。
- 流式 Provider：tool_call(preparing) → tool_args(delta×N) → tool_result。
- 审批暂停（D13）：awaiting_approval，流停止不发 done。
- 工具失败 / 递归耗尽 → error + done。
- emit 队列指令（component/navigate）透传。
"""
import asyncio

import pytest
from langchain_core.messages import AIMessage, ToolCallChunk, ToolMessage

from app.agents.sse_adapter import (
    HEARTBEAT,
    PHASE_AWAITING_APPROVAL,
    PHASE_EXECUTING,
    PHASE_REPLY,
    RECURSION_FRIENDLY_ERROR,
    agent_events_to_sse,
)


class _Chunk:
    def __init__(self, content="", tool_call_chunks=None):
        self.content = content
        self.tool_call_chunks = tool_call_chunks or []


class _Chunks:
    """模拟 astream_events 流：可迭代的事件字典列表。"""

    def __init__(self, events):
        self._events = events

    def __aiter__(self):
        self._it = iter(self._events)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration


async def _collect(events, **kwargs):
    frames = []
    async for frame in agent_events_to_sse(_Chunks(events), **kwargs):
        frames.append(frame)
    return frames


def _tool_result_msg(call_id="c1", content='{"ok": true}'):
    return ToolMessage(content=content, tool_call_id=call_id)


def _ai_tool_calls(call_id="c1", name="search_exam_bank", args=None):
    return AIMessage(content="", tool_calls=[
        {"name": name, "args": args or {"keyword": "氧化还原"}, "id": call_id, "type": "tool_call"}
    ])


# ---------- 非流式 Provider（D12 降级） ----------

def test_non_streaming_tool_turn_sequence():
    events = [
        {"event": "on_chat_model_end", "data": {"output": _ai_tool_calls()}},
        {"event": "on_tool_start", "name": "search_exam_bank"},
        {"event": "on_tool_end", "name": "search_exam_bank", "data": {"output": _tool_result_msg()}},
        {"event": "on_chat_model_end", "data": {"output": AIMessage(content="已搜索到题目")}},
    ]
    frames = asyncio.run(_collect(events))
    names = [n for n, _ in frames]
    assert names == [
        "tool_call", "phase", "tool_result", "phase", "text", "done",
    ]
    # 非流式：单次 tool_call 携带完整参数（D12）
    tc = frames[0][1]
    assert tc["status"] == "preparing"
    assert tc["args"]["keyword"] == "氧化还原"
    assert tc["tool"] == "search_exam_bank"
    # on_tool_start → executing
    assert frames[1][1]["content"] == PHASE_EXECUTING
    # tool_result 成功
    tr = frames[2][1]
    assert tr["success"] is True
    assert tr["result"]["ok"] is True
    # 文本回复
    assert frames[4][0] == "text"
    assert frames[4][1]["content"] == "已搜索到题目"
    assert frames[5] == ("done", {"type": "done"})


def test_non_streaming_stream_tool_args_disabled():
    # stream_tool_args=False：即使 chunk 流存在也不发 tool_args，回退单次 tool_call
    chunk = _Chunk(content="", tool_call_chunks=[
        ToolCallChunk(index=0, name="web_search", args='{"q":"化学"}', id="c1")
    ])
    events = [
        {"event": "on_chat_model_stream", "data": {"chunk": chunk}},
        {"event": "on_chat_model_end", "data": {"output": _ai_tool_calls("c1", "web_search", {"q": "化学"})}},
        {"event": "on_tool_start", "name": "web_search"},
        {"event": "on_tool_end", "name": "web_search", "data": {"output": _tool_result_msg("c1")}},
        {"event": "on_chat_model_end", "data": {"output": AIMessage(content="结果")}},
    ]
    frames = asyncio.run(_collect(events, stream_tool_args=False))
    names = [n for n, _ in frames]
    assert "tool_args" not in names
    assert names.count("tool_call") == 1
    assert frames[0][1]["args"]["q"] == "化学"


# ---------- 流式 Provider（D12 tool_args 增量） ----------

def test_streaming_tool_args_deltas():
    chunk1 = _Chunk(content="", tool_call_chunks=[
        ToolCallChunk(index=0, name="search_exam_bank", args='{"keyword"', id="c1")
    ])
    chunk2 = _Chunk(content="", tool_call_chunks=[
        ToolCallChunk(index=0, name="", args=':"氧化"}', id="c1")
    ])
    events = [
        {"event": "on_chat_model_stream", "data": {"chunk": chunk1}},
        {"event": "on_chat_model_stream", "data": {"chunk": chunk2}},
        {"event": "on_chat_model_end", "data": {"output": _ai_tool_calls()}},
        {"event": "on_tool_start", "name": "search_exam_bank"},
        {"event": "on_tool_end", "name": "search_exam_bank", "data": {"output": _tool_result_msg()}},
        {"event": "on_chat_model_end", "data": {"output": AIMessage(content="完成")}},
    ]
    frames = asyncio.run(_collect(events))
    names = [n for n, _ in frames]
    assert names[0] == "tool_call"
    # tool_call 携带工具名（首 chunk 即含 name）
    assert frames[0][1]["name"] == "search_exam_bank"
    assert names[1] == "tool_args"
    assert names[2] == "tool_args"
    assert names[3] == "phase"  # executing
    assert names[4] == "tool_result"
    # 流式后 on_chat_model_end 不再重复发 tool_call（仅一次）
    assert names.count("tool_call") == 1
    # 参数增量合并
    assert frames[1][1]["delta"] == '{"keyword"'
    assert frames[2][1]["delta"] == ':"氧化"}'


def test_text_stream_with_tool_args_mixed():
    # 文本与工具参数同 chunk 流：文本 → phase(reply)+text，工具 → tool_args
    chunk = _Chunk(content="正在搜索", tool_call_chunks=[
        ToolCallChunk(index=0, name="web_search", args="{}", id="c1")
    ])
    events = [
        {"event": "on_chat_model_stream", "data": {"chunk": chunk}},
        {"event": "on_chat_model_end", "data": {"output": _ai_tool_calls("c1", "web_search", {})}},
        {"event": "on_tool_start", "name": "web_search"},
        {"event": "on_tool_end", "name": "web_search", "data": {"output": _tool_result_msg("c1")}},
        {"event": "on_chat_model_end", "data": {"output": AIMessage(content="搞定")}},
    ]
    frames = asyncio.run(_collect(events))
    texts = [p["content"] for n, p in frames if n == "text"]
    assert "正在搜索" in texts
    assert texts[-1] == "搞定"


# ---------- 审批暂停（D13） ----------

def test_approval_pause_stops_stream_without_done():
    blocked = {
        "error": "requires_approval_blocked",
        "tool": "delete_bank",
        "args": {"bank_name": "高一氧化还原"},
    }
    events = [
        {"event": "on_chat_model_end", "data": {"output": _ai_tool_calls("c1", "delete_bank", {"bank_name": "高一氧化还原"})}},
        {"event": "on_tool_start", "name": "delete_bank"},
        {"event": "on_tool_end", "name": "delete_bank", "data": {"output": _tool_result_msg("c1", content='{"error": "requires_approval_blocked", "tool": "delete_bank", "args": {"bank_name": "高一氧化还原"}}')}},
    ]
    pending = []
    frames = asyncio.run(_collect(
        events, on_approval_pending=lambda tool, args: pending.append((tool, args))
    ))
    names = [n for n, _ in frames]
    # tool_call → phase(executing) → awaiting_approval(独立事件) → tool_result
    assert names == ["tool_call", "phase", "awaiting_approval", "tool_result"]
    # 不发出 done——流在审批处暂停
    assert "done" not in names
    # executing 先于 awaiting_approval
    assert frames[1][1]["content"] == PHASE_EXECUTING
    pause_ev = frames[2]
    assert pause_ev[0] == PHASE_AWAITING_APPROVAL
    assert pause_ev[1]["tool"] == "delete_bank"
    assert pause_ev[1]["args"] == {"bank_name": "高一氧化还原"}
    # tool_result 失败，携带 error
    assert frames[3][1]["success"] is False
    assert frames[3][1]["error"] == "requires_approval_blocked"
    # 回调记录待审批
    assert pending == [("delete_bank", {"bank_name": "高一氧化还原"})]


# ---------- 工具失败 / 递归耗尽 ----------

def test_tool_error_yields_fail_and_text():
    events = [
        {"event": "on_tool_start", "name": "web_search"},
        {"event": "on_tool_error", "name": "web_search", "data": {"error": RuntimeError("网络断开")}},
        {"event": "on_chat_model_end", "data": {"output": AIMessage(content="稍后重试")}},
    ]
    frames = asyncio.run(_collect(events))
    names = [n for n, _ in frames]
    tr = frames[1][1]
    assert tr["success"] is False
    assert "网络断开" in tr["error"]
    assert names[-1] == "done"


def test_recursion_error_friendly_message():
    class _Boom(_Chunks):
        async def __anext__(self):
            raise RuntimeError("GraphRecursionError: Recursion limit 12 reached")

    async def _collect_direct():
        frames = []
        async for frame in agent_events_to_sse(_Boom([])):
            frames.append(frame)
        return frames

    frames = asyncio.run(_collect_direct())
    names = [n for n, _ in frames]
    assert names == ["error", "done"]
    assert frames[0][1]["message"] == RECURSION_FRIENDLY_ERROR
    assert frames[0][1]["recoverable"] is True


def test_generic_error_yields_error_and_done():
    class _Boom(_Chunks):
        async def __anext__(self):
            raise RuntimeError("provider down")

    async def _collect_direct():
        frames = []
        async for frame in agent_events_to_sse(_Boom([])):
            frames.append(frame)
        return frames

    frames = asyncio.run(_collect_direct())
    names = [n for n, _ in frames]
    assert names == ["error", "done"]
    assert "provider down" in frames[0][1]["message"]


# ---------- emit 队列指令透传 ----------

def test_emit_queue_component_drained():
    import queue

    q = queue.Queue()
    q.put(("component", {"component": "exam_workbench", "params": {"kp": "氧化"}}))
    q.put(("navigate", {"page": "student", "params": {}}))
    events = [
        {"event": "on_chat_model_end", "data": {"output": AIMessage(content="打开面板")}},
    ]
    frames = asyncio.run(_collect(events, emit_queue=q))
    names = [n for n, _ in frames]
    assert "component" in names
    assert "navigate" in names
    comp = next(p for n, p in frames if n == "component")
    assert comp["component"] == "exam_workbench"


def test_payload_type_field_present():
    events = [
        {"event": "on_chat_model_end", "data": {"output": AIMessage(content="hi")}},
    ]
    frames = asyncio.run(_collect(events))
    for name, payload in frames:
        assert "type" in payload, f"{name} payload 缺 type"


# ---------- 心跳保活 ----------

class _SlowChunks(_Chunks):
    """模拟慢工具执行期的长静默：相邻事件间 sleep 超过 heartbeat_interval。"""

    async def __anext__(self):
        await asyncio.sleep(0.12)
        return await super().__anext__()


async def _collect_slow(events, **kwargs):
    frames = []
    async for frame in agent_events_to_sse(_SlowChunks(events), **kwargs):
        frames.append(frame)
    return frames


def test_heartbeat_frames_during_silent_period():
    events = [
        {"event": "on_chat_model_end", "data": {"output": _ai_tool_calls()}},
        {"event": "on_tool_start", "name": "search_exam_bank"},
        {"event": "on_tool_end", "name": "search_exam_bank", "data": {"output": _tool_result_msg()}},
        {"event": "on_chat_model_end", "data": {"output": AIMessage(content="完成")}},
    ]
    frames = asyncio.run(_collect_slow(events, heartbeat_interval=0.05))
    names = [n for n, _ in frames]
    # 静默 > heartbeat_interval → 心跳帧（空 payload 保活，前端忽略）
    assert HEARTBEAT in names
    hb = next(p for n, p in frames if n == HEARTBEAT)
    assert hb == {}
    # 心跳仅穿插于事件间，真实事件顺序不受影响，正常收尾 done
    real = [n for n in names if n != HEARTBEAT]
    assert real == ["tool_call", "phase", "tool_result", "phase", "text", "done"]
    assert names[-1] == "done"
