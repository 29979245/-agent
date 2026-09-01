"""上下文管理（doc 30 §8 + §9.3 / design D7）。

三层裁剪：消息 >30 条时——
  Layer 1 无条件保留最近 6 条（约 3 轮）；
  Layer 2 更早消息含教学关键词的额外保留；
  Layer 3 被丢弃 ≥10 条 → LLM 压缩为 ≤200 字中文摘要（失败则直接丢弃）。

裁剪后结构：`[摘要] → [关键词命中历史] → [最近 6 条] → [当前输入]`，写回对话检查点。

消息组装：`[学生档案 System] → [情景记忆 System] → 裁剪后历史 → 当前输入`（§9.2）。
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

TRIM_THRESHOLD = 30
KEEP_RECENT = 6
SUMMARY_MAX_CHARS = 200
SUMMARY_MIN_DROPPED = 10

TEACHING_KEYWORDS = (
    "学生", "诊断", "障碍", "考试", "题目", "知识点", "分数",
    "薄弱", "学习计划", "错题", "成绩", "练习", "班级", "教师",
)

Summarizer = Callable[[list[dict]], Awaitable[str]]


def _message_text(message: dict) -> str:
    content = message.get("content") if isinstance(message, dict) else getattr(message, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content)
    return str(content or "")


def keyword_hit(message: dict) -> bool:
    text = _message_text(message)
    return any(kw in text for kw in TEACHING_KEYWORDS)


def _to_plain_messages(messages: list[dict]) -> list[dict]:
    """LangChain 消息与普通字典统一为 {role, content} 文本形式。"""
    out = []
    for m in messages:
        role = m.get("role") if isinstance(m, dict) else getattr(m, "type", "user")
        if role == "human":
            role = "user"
        elif role == "ai":
            role = "assistant"
        out.append({"role": role, "content": _message_text(m)})
    return out


def trim_context(messages: list[dict]) -> tuple[list[dict], list[dict]]:
    """三层裁剪。返回 (裁剪后消息, 被丢弃消息)；未达阈值返回 (原列表, [])。"""
    msgs = _to_plain_messages(messages)
    if len(msgs) <= TRIM_THRESHOLD:
        return msgs, []
    recent = msgs[-KEEP_RECENT:]
    older = msgs[:-KEEP_RECENT]
    kept = [m for m in older if keyword_hit(m)]
    dropped = [m for m in older if not keyword_hit(m)]
    # 结构：关键词命中历史（older 中命中的）→ 最近 6 条
    trimmed = kept + recent
    return trimmed, dropped


async def compress_summary(
    dropped: list[dict],
    summarizer: Summarizer | None = None,
) -> str | None:
    """Layer 3：对 ≥10 条丢弃消息生成 ≤200 字摘要；失败/无 summarizer 返回 None（丢弃）。"""
    if len(dropped) < SUMMARY_MIN_DROPPED or not dropped:
        return None
    if summarizer is None:
        return None
    try:
        text = await summarizer(dropped)
    except Exception:  # noqa: BLE001 —— 摘要失败直接丢弃（doc 30 §9.3）
        logger.warning("[Context] LLM 摘要失败，丢弃更早消息", exc_info=True)
        return None
    return (text or "")[:SUMMARY_MAX_CHARS]


def assemble_messages(
    history: list[dict],
    current_input: str,
    *,
    profile_message: dict | None = None,
    episodic_message: dict | None = None,
    summary: str | None = None,
) -> list[dict]:
    """按 §9.2 组装消息列表：档案 → 情景 → 摘要 → 历史 → 当前输入。"""
    messages: list[dict] = []
    if profile_message:
        messages.append(profile_message)
    if episodic_message:
        messages.append(episodic_message)
    if summary:
        messages.append({"role": "system", "content": f"对话摘要：{summary}"})
    messages.extend(history)
    messages.append({"role": "user", "content": current_input})
    return messages


class ContextManager:
    """对话上下文管理器：三层裁剪 + 消息组装，写回检查点由调用方驱动。"""

    def __init__(self, summarizer: Summarizer | None = None) -> None:
        self._summarizer = summarizer

    def bind_summarizer(self, summarizer: Summarizer) -> None:
        self._summarizer = summarizer

    async def prepare(
        self,
        history: list[dict],
        current_input: str,
        *,
        profile_message: dict | None = None,
        episodic_message: dict | None = None,
    ) -> dict:
        """准备一次调用的消息列表。返回 {messages, dropped, summary, trimmed}。"""
        trimmed, dropped = trim_context(history)
        summary = await compress_summary(dropped, self._summarizer)
        messages = assemble_messages(
            trimmed,
            current_input,
            profile_message=profile_message,
            episodic_message=episodic_message,
            summary=summary,
        )
        return {
            "messages": messages,
            "dropped": dropped,
            "summary": summary,
            "trimmed": trimmed,
        }
