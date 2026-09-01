"""SSE 适配器（doc 30 §13.1 / doc 41 §3.3 / design D3/D12，ADR-0009）。

把 LangGraph `astream_events` 事件流转换为 Agent SSE 13 事件：
  phase / tool_call / tool_args / tool_result / text / component / navigate /
  populate / action / exam_images / awaiting_approval / error / done

- 工具调用三连：tool_call(preparing) → tool_args(delta×N) → tool_result(completed/failed)。
- D12 降级：模型不流式返回工具参数时，退化为单次 tool_call（完整参数内联）→ 直接 tool_result。
- 审批暂停（D13）：工具结果含 requires_approval_blocked → 发独立 awaiting_approval 事件，
  停止流（不发 done），记录待审批。
- 心跳保活：慢工具（如出题 LLM 调用）执行期 SSE 可能静默数十秒，期间定期发
  `_heartbeat` 帧（端点渲染为 `: ping` 注释），防止空闲超时被掐断 → 前端「连接中断」。
- 递归耗尽：LangGraph GraphRecursionError → 用户友好 error + done。

产出为 `(event_name, payload)` 元组流；端点负责格式化为 `event:/data:` SSE 帧。
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncIterator, Callable, Optional

logger = logging.getLogger(__name__)

PHASE_THINKING = "thinking"
PHASE_EXECUTING = "executing"
PHASE_PLANNING = "planning"
PHASE_REPLY = "reply"
PHASE_AWAITING_APPROVAL = "awaiting_approval"

RECURSION_FRIENDLY_ERROR = "处理超时，Agent 重试次数用尽。请重试或换个方式提问。"

# 心跳帧事件名：端点渲染为 SSE 注释 `: ping`（前端 parseSSE 忽略，仅保活）
HEARTBEAT = "_heartbeat"
HEARTBEAT_INTERVAL = 10.0  # 事件流静默超过该秒数发一次心跳


def _phase(content: str) -> tuple[str, dict]:
    return "phase", {"type": "phase", "content": content}


def _awaiting_approval(tool: str, args: dict, call_id: str = "") -> tuple[str, dict]:
    """审批暂停事件（D13）：独立 `awaiting_approval` 事件名，前端据此置 paused（非断连）。"""
    return PHASE_AWAITING_APPROVAL, {
        "type": PHASE_AWAITING_APPROVAL,
        "tool": tool,
        "args": args or {},
        "tool_call_id": call_id,
    }


def _text(content: str) -> tuple[str, dict]:
    return "text", {"type": "text", "content": content}


def _tool_call(name: str, call_id: str, args: dict) -> tuple[str, dict]:
    return "tool_call", {
        "type": "tool_call",
        "tool": name,
        "name": name,
        "id": call_id,
        "tool_call_id": call_id,
        "args": args or {},
        "status": "preparing",
    }


def _tool_args(call_id: str, delta: str) -> tuple[str, dict]:
    return "tool_args", {"type": "tool_args", "tool_call_id": call_id, "delta": delta}


def _tool_result(name: str, call_id: str, success: bool, result: Any, error: str = "") -> tuple[str, dict]:
    return "tool_result", {
        "type": "tool_result",
        "tool": name,
        "name": name,
        "id": call_id,
        "tool_call_id": call_id,
        "success": bool(success),
        "result": result,
        "error": error,
    }


def _error(message: str, recoverable: bool = True) -> tuple[str, dict]:
    return "error", {"type": "error", "message": message, "recoverable": recoverable}


def _done() -> tuple[str, dict]:
    return "done", {"type": "done"}


def _heartbeat() -> tuple[str, dict]:
    return HEARTBEAT, {}


def _tool_call_id_from_chunk(chunk) -> str:
    """从流式 chunk 的 tool_call_chunks 提取 call id。ToolCallChunk 是 dict 子类。"""
    for tcc in getattr(chunk, "tool_call_chunks", []) or []:
        if tcc.get("id"):
            return tcc["id"]
    return ""


def _chunk_content(chunk) -> str:
    content = getattr(chunk, "content", None)
    if content is None and isinstance(chunk, dict):
        content = chunk.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and "text" in item:
                parts.append(item["text"])
            else:
                parts.append(str(item))
        return "".join(parts)
    return str(content or "")


def _tool_calls_from_message(msg) -> list[dict]:
    return list(getattr(msg, "tool_calls", None) or [])


def _try_json(text: str) -> Any:
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None


def _summarize_tool_output(output) -> Any:
    """ToolMessage 内容解析：JSON 字典原样返回（前端富渲染），否则返回字符串。"""
    content = getattr(output, "content", None)
    if content is None:
        return output
    if isinstance(content, str):
        parsed = _try_json(content)
        return parsed if isinstance(parsed, dict) else content
    return content


async def agent_events_to_sse(
    events: AsyncIterator[dict],
    *,
    emit_queue: Optional[Any] = None,
    on_approval_pending: Optional[Callable[[str, dict], None]] = None,
    stream_tool_args: bool = True,
    heartbeat_interval: float = HEARTBEAT_INTERVAL,
) -> AsyncIterator[tuple[str, dict]]:
    """把 astream_events 事件流转换为 Agent SSE 事件元组流。

    events: `agent.astream_events(inputs, config=..., version="v2")`。
    emit_queue: 工具经 ToolContext.emit 推入的 component/navigate 等指令队列。
    on_approval_pending(tool, args): 审批暂停时回调（记录待审批）。
    stream_tool_args: Provider capabilities.stream_tool_args（D12）。
    heartbeat_interval: 事件流静默超过该秒数发一次心跳帧（保活）。测试可调小。
    """
    streamed_call_ids: set[str] = set()  # 已通过 tool_args 流式推送的 call id
    emitting_text = False  # 本模型调用是否已在产出文本（决定 phase=reply）
    streamed_any_tool = False  # 本次迭代是否流式推送过工具参数

    async def _drain_emit():
        if emit_queue is None:
            return
        while not emit_queue.empty():
            try:
                name, payload = emit_queue.get_nowait()
            except Exception:  # noqa: BLE001
                break
            if isinstance(name, str) and name in {
                "component", "navigate", "populate", "action", "exam_images",
            }:
                yield name, payload

    # 心跳保活：慢工具执行期 SSE 可能静默数十秒。用独立 reader 任务拉取事件流，
    # 主循环按 heartbeat_interval 从队列取——超时只取消 queue.get()，不会像
    # wait_for(events.__anext__()) 那样把 CancelledError 抛进 LangGraph 生成器
    # 导致流提前终止。
    q: asyncio.Queue = asyncio.Queue()
    _END = object()

    async def _reader() -> None:
        try:
            ait = events.__aiter__()
            while True:
                q.put_nowait(await ait.__anext__())
        except StopAsyncIteration:
            q.put_nowait(_END)
        except Exception as exc:  # noqa: BLE001 —— 流内异常经队列转交主循环处理
            q.put_nowait(exc)

    reader = asyncio.create_task(_reader())
    try:
        while True:
            try:
                ev = await asyncio.wait_for(q.get(), timeout=heartbeat_interval)
            except asyncio.TimeoutError:
                yield _heartbeat()
                continue
            if isinstance(ev, BaseException):
                raise ev
            if ev is _END:
                break

            async for frame in _drain_emit():
                yield frame

            etype = ev.get("event")
            data = ev.get("data", {})
            chunk = data.get("chunk")

            if etype == "on_chat_model_stream" and chunk is not None:
                # 文本流 → phase=reply + text token
                content = _chunk_content(chunk)
                if content:
                    if not emitting_text:
                        yield _phase(PHASE_REPLY)
                        emitting_text = True
                    yield _text(content)

                # 工具参数流 → tool_call(preparing) + tool_args delta（D3/ADR-0009）
                tccs = getattr(chunk, "tool_call_chunks", None) or []
                if tccs and stream_tool_args:
                    streamed_any_tool = True
                    for tcc in tccs:
                        if not tcc.get("id"):
                            continue
                        call_id = tcc["id"]
                        if call_id not in streamed_call_ids:
                            streamed_call_ids.add(call_id)
                            yield _tool_call(tcc.get("name") or "", call_id, {})
                        delta = tcc.get("args") or ""
                        if delta:
                            yield _tool_args(call_id, delta)

            elif etype == "on_chat_model_end":
                msg = data.get("output")
                tool_calls = _tool_calls_from_message(msg) if msg is not None else []
                if tool_calls:
                    # 非流式 Provider（D12 降级）：单次 tool_call（完整参数内联）
                    if not streamed_any_tool or not stream_tool_args:
                        for tc in tool_calls:
                            call_id = tc.get("id") or f"call_{id(tc)}"
                            yield _tool_call(tc.get("name") or "", call_id, tc.get("args") or {})
                # 非流式 Provider 的最终文本（D12：仅 on_chat_model_end，无 stream 事件）；
                # 已流式输出过文本时不重复。
                content = _chunk_content(msg) if msg is not None else ""
                if content and not emitting_text:
                    yield _phase(PHASE_REPLY)
                    yield _text(content)
                # 重置本轮状态
                emitting_text = False
                streamed_any_tool = False

            elif etype == "on_tool_start":
                yield _phase(PHASE_EXECUTING)

            elif etype == "on_tool_end":
                name = ev.get("name") or ""
                out = data.get("output")
                call_id = getattr(out, "tool_call_id", None) or ""
                parsed = _summarize_tool_output(out)
                if isinstance(parsed, dict) and parsed.get("error") == "requires_approval_blocked":
                    # D13：审批暂停——发独立 awaiting_approval 事件（前端 paused 契约），不发 done
                    tool_name = parsed.get("tool") or name
                    args = parsed.get("args") or {}
                    yield _awaiting_approval(tool_name, args, call_id)
                    yield _tool_result(name, call_id, False, parsed, error="requires_approval_blocked")
                    if on_approval_pending is not None:
                        on_approval_pending(tool_name, args)
                    return
                yield _tool_result(name, call_id, True, parsed)

            elif etype == "on_tool_error":
                name = ev.get("name") or ""
                err = data.get("error")
                yield _tool_result(name, "", False, None, error=str(err or "工具执行失败"))
                yield _text(f"工具执行失败：{err}")

        async for frame in _drain_emit():
            yield frame
        yield _done()
    except Exception as exc:  # noqa: BLE001 —— 递归耗尽/Provider 全败等
        if "recursion" in str(exc).lower():
            logger.warning("[SSE] 递归耗尽：%s", exc)
            yield _error(RECURSION_FRIENDLY_ERROR, recoverable=True)
        else:
            logger.warning("[SSE] 对话流异常：%s", exc)
            yield _error(str(exc), recoverable=True)
        yield _done()
    finally:
        if not reader.done():
            reader.cancel()
