"""Agent LLMClient → .complete(messages) 适配器（周报/诊断等既有同步服务依赖）。

把 Agent 的 LLMClient（异步 acomplete_chain / 三级回退）适配为同步 .complete(messages)，
供 tools_diagnosis / tools_parent_report 等复用既有同步报告管线。
使用同步 complete_chain：不驱动事件循环，在 async 工具上下文内调用安全。
"""
from __future__ import annotations


class CompleteAdapter:
    """把 Agent LLMClient 适配为 .complete(messages)->str。"""

    def __init__(self, llm) -> None:
        self._llm = llm

    def complete(self, messages: list[dict]) -> str:
        return self._llm.complete_chain(messages)
