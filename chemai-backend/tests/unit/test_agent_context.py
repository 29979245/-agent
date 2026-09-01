"""上下文裁剪与消息组装单测（doc 30 §8/§9.2/§9.3）。

重点覆盖 LangChain BaseMessage（HumanMessage/AIMessage）与普通 dict 混用的场景——
第 2+ 轮对话从检查点读回的是 BaseMessage，此前 `m.get("role")` 会 AttributeError。
"""
import asyncio

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.agents.context import (
    TRIM_THRESHOLD,
    ContextManager,
    _message_text,
    _to_plain_messages,
    assemble_messages,
    keyword_hit,
    trim_context,
)


class TestMessageText:
    def test_plain_str(self):
        assert _message_text({"role": "user", "content": "你好"}) == "你好"

    def test_content_blocks_list(self):
        msg = {"role": "user", "content": [{"type": "text", "text": "甲"}, {"type": "text", "text": "乙"}]}
        assert _message_text(msg) == "甲乙"

    def test_human_message(self):
        assert _message_text(HumanMessage(content="你好")) == "你好"

    def test_human_message_blocks(self):
        msg = HumanMessage(content=[{"type": "text", "text": "配平"}, {"type": "text", "text": "方程式"}])
        assert _message_text(msg) == "配平方程式"

    def test_none_content(self):
        assert _message_text({"role": "user", "content": None}) == ""


class TestToPlainMessages:
    def test_dict_messages(self):
        out = _to_plain_messages([{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}])
        assert out == [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]

    def test_base_message_objects(self):
        """回归：HumanMessage 无 .get，此前在此崩溃。"""
        out = _to_plain_messages([HumanMessage(content="问"), AIMessage(content="答")])
        assert out == [{"role": "user", "content": "问"}, {"role": "assistant", "content": "答"}]

    def test_mixed_dict_and_base_message(self):
        out = _to_plain_messages([{"role": "user", "content": "a"}, AIMessage(content="b")])
        assert out == [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]


class TestTrimContext:
    def test_under_threshold_untouched(self):
        msgs = [{"role": "user", "content": f"m{i}"} for i in range(TRIM_THRESHOLD - 1)]
        trimmed, dropped = trim_context(msgs)
        assert trimmed == msgs
        assert dropped == []

    def test_over_threshold_keeps_recent_six(self):
        msgs = [{"role": "user", "content": f"m{i}"} for i in range(TRIM_THRESHOLD + 4)]
        trimmed, dropped = trim_context(msgs)
        assert len(trimmed) == 6
        assert trimmed[0]["content"] == f"m{TRIM_THRESHOLD - 2}"
        assert dropped == msgs[:-6]

    def test_base_message_history_over_threshold(self):
        msgs = [HumanMessage(content=f"m{i}") for i in range(TRIM_THRESHOLD + 4)]
        trimmed, dropped = trim_context(msgs)
        assert len(trimmed) == 6
        assert all(m["role"] == "user" for m in trimmed)
        assert len(dropped) == TRIM_THRESHOLD - 2

    def test_keyword_hit_kept_before_recent(self):
        # 关键词消息放在 older（不在最近 6 条内），应被额外保留
        msgs = [{"role": "user", "content": f"普通消息 {i}"} for i in range(22)]
        msgs.append({"role": "user", "content": "学生薄弱知识点诊断"})
        msgs.extend({"role": "user", "content": f"普通消息 {i}"} for i in range(22, 30))
        assert len(msgs) == 31
        trimmed, dropped = trim_context(msgs)
        # recent 6 条（全部为普通消息，indices 25..30）+ older 中命中的 1 条
        assert len(trimmed) == 7
        assert trimmed[0]["content"] == "学生薄弱知识点诊断"
        assert len(dropped) == 24
        assert all("学生" not in m["content"] for m in dropped)


class TestKeywordHit:
    def test_teaching_keyword(self):
        assert keyword_hit({"role": "user", "content": "查看学生成绩"})

    def test_plain_chat_no_keyword(self):
        assert not keyword_hit({"role": "user", "content": "今天天气如何"})

    def test_base_message(self):
        assert keyword_hit(HumanMessage(content="这道错题怎么讲"))


class TestAssembleMessages:
    def test_order(self):
        msgs = assemble_messages(
            [{"role": "user", "content": "历史"}],
            "当前",
            profile_message={"role": "system", "content": "档案"},
            episodic_message={"role": "system", "content": "情景"},
            summary="摘要",
        )
        roles = [m["role"] for m in msgs]
        assert roles == ["system", "system", "system", "user", "user"]
        assert msgs[0]["content"] == "档案"
        assert msgs[1]["content"] == "情景"
        assert msgs[2]["content"] == "对话摘要：摘要"
        assert msgs[-1]["content"] == "当前"

    def test_minimal(self):
        msgs = assemble_messages([], "当前")
        assert msgs == [{"role": "user", "content": "当前"}]


class TestContextManager:
    def test_prepare_with_base_message_history(self):
        async def run():
            cm = ContextManager()
            return await cm.prepare(
                [HumanMessage(content="甲"), AIMessage(content="乙")],
                "丙",
                profile_message={"role": "system", "content": "档案"},
            )

        result = asyncio.run(run())
        roles = [m["role"] for m in result["messages"]]
        assert roles == ["system", "user", "assistant", "user"]
        assert result["dropped"] == []
        assert result["summary"] is None

    def test_prepare_over_threshold_with_summarizer(self):
        async def run():
            async def fake_summarizer(dropped):
                return "已压缩的更早对话摘要"

            cm = ContextManager(summarizer=fake_summarizer)
            history = [HumanMessage(content=f"m{i}") for i in range(TRIM_THRESHOLD + 12)]
            return await cm.prepare(history, "当前")

        result = asyncio.run(run())
        assert result["summary"] == "已压缩的更早对话摘要"
        assert result["messages"][0]["content"] == "对话摘要：已压缩的更早对话摘要"

    def test_summarizer_failure_returns_none(self):
        async def run():
            async def broken_summarizer(dropped):
                raise RuntimeError("llm down")

            cm = ContextManager(summarizer=broken_summarizer)
            history = [{"role": "user", "content": f"m{i}"} for i in range(TRIM_THRESHOLD + 12)]
            return await cm.prepare(history, "当前")

        result = asyncio.run(run())
        assert result["summary"] is None
