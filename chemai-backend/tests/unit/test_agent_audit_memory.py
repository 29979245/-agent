"""审计日志 + 记忆系统单测（doc 30 §9/§10，tasks 记忆/审计，审查补丁 11.8）。"""
import asyncio
import os

import pytest

from app.agents.audit import AuditLogger, mask_args, summarize_result
from app.agents.context import (
    ContextManager,
    KEEP_RECENT,
    TRIM_THRESHOLD,
    assemble_messages,
    compress_summary,
    keyword_hit,
    trim_context,
)
from app.agents.memory import (
    DIAGNOSIS_HISTORY_MAX_ITEMS,
    EpisodicMemory,
    LongTermStore,
    MemorySystem,
    WorkingMemory,
)


# ---------- 审计 ----------

def test_mask_sensitive_fields():
    args = {"keyword": "氧化", "phone": "13800000000", "nested": {"api_key": "sk-123"}, "token": "t"}
    masked = mask_args(args)
    assert masked["phone"] == "***"
    assert masked["token"] == "***"
    assert masked["nested"]["api_key"] == "***"
    assert masked["keyword"] == "氧化"


def test_summarize_truncates_200():
    long_text = "汉" * 300
    summary = summarize_result(long_text)
    assert len(summary) <= 200 + 1  # +1 省略号


def test_summarize_array_count():
    assert summarize_result([1, 2, 3]) == "共 3 项"


def test_audit_ring_buffer_maxlen():
    logger = AuditLogger(maxlen=3)
    for i in range(5):
        logger.log(persona="teacher", skill_name=f"t{i}", duration_ms=1.0)
    recent = logger.recent()
    assert len(recent) == 3
    assert recent[-1]["skill_name"] == "t4"


def test_audit_best_effort_unwritable_dir(tmp_path):
    logger = AuditLogger(audit_dir=tmp_path / "sub" / "nested")  # 目录不存在，会创建
    logger.log(persona="tutor", skill_name="web_search", args={"q": "x"})
    assert logger.recent()[-1]["skill_name"] == "web_search"


def test_audit_writes_jsonl(tmp_path):
    logger = AuditLogger(audit_dir=tmp_path)
    logger.log(persona="teacher", skill_name="generate_questions", args={"kp": "氧化还原", "phone": "1"}, duration_ms=12.5)
    files = list((tmp_path).glob("agent_audit_*.jsonl"))
    assert files
    import json
    with open(files[0], encoding="utf-8") as fh:
        entry = json.loads(fh.readline())
    assert entry["persona"] == "teacher"
    assert entry["args"]["phone"] == "***"


# ---------- 审计接线（execute_tool 每次执行落审计，doc 30 §10） ----------


def _audit_stub():
    from app.agents.tools import registry

    calls = []

    class _Stub:
        def log(self, **kw):
            calls.append(kw)

    return registry, _Stub(), calls


def test_execute_tool_audits_success(monkeypatch):
    from app.agents.tools.context import ToolContext

    registry, stub, calls = _audit_stub()
    monkeypatch.setattr(registry, "audit_logger", stub)
    registry.TOOL_IMPLS["_test_audit"] = lambda ctx, **kw: {"result": kw.get("v"), "_component": "x"}
    registry.TOOL_SCHEMAS["_test_audit"] = dict
    ctx = ToolContext(persona="teacher", user={"user_id": 1, "role": "teacher"})
    try:
        out = asyncio.run(registry.execute_tool(ctx, "_test_audit", {"v": 1}))
        assert out == {"result": 1}  # _component 剥离后返回纯净结果
        assert len(calls) == 1
        entry = calls[0]
        assert entry["persona"] == "teacher"
        assert entry["skill_name"] == "_test_audit"
        assert entry["args"] == {"v": 1}
        assert entry["result_summary"] == {"result": 1}
        assert entry["duration_ms"] >= 0
    finally:
        registry.TOOL_IMPLS.pop("_test_audit", None)
        registry.TOOL_SCHEMAS.pop("_test_audit", None)


def test_execute_tool_audits_failure(monkeypatch):
    from app.agents.tools.context import ToolContext

    registry, stub, calls = _audit_stub()
    monkeypatch.setattr(registry, "audit_logger", stub)

    def _boom(ctx, **kw):
        raise RuntimeError("engine down")

    registry.TOOL_IMPLS["_test_boom"] = _boom
    registry.TOOL_SCHEMAS["_test_boom"] = dict
    ctx = ToolContext(persona="student", user={"user_id": 2, "role": "student"})
    try:
        out = asyncio.run(registry.execute_tool(ctx, "_test_boom", {"q": "x"}))
        assert out["error"] == "tool_failed"
        assert len(calls) == 1
        entry = calls[0]
        assert entry["persona"] == "student"
        assert entry["error"] == "engine down"
    finally:
        registry.TOOL_IMPLS.pop("_test_boom", None)
        registry.TOOL_SCHEMAS.pop("_test_boom", None)


def test_execute_tool_guard_blocked_not_audited(monkeypatch):
    """审批阻塞未启动执行 → 不登记执行键也不落审计（D11 语义）。"""
    from app.agents.tools.context import ToolContext

    registry, stub, calls = _audit_stub()
    monkeypatch.setattr(registry, "audit_logger", stub)
    ctx = ToolContext(persona="teacher", user={"user_id": 1, "role": "teacher"})
    out = asyncio.run(registry.execute_tool(ctx, "delete_bank", {"bank_id": 3}))
    assert out["error"] == "requires_approval_blocked"
    assert calls == []


# ---------- 记忆 ----------

def test_working_memory_sliding_window():
    wm = WorkingMemory(maxlen=3)
    for i in range(5):
        wm.add({"role": "user", "content": str(i)})
    assert [m["content"] for m in wm.messages] == ["2", "3", "4"]


def test_episodic_memory_system_message():
    ep = EpisodicMemory()
    assert ep.as_system_message() is None
    ep.record("diagnosis", {"barrier_type": "concept"})
    msg = ep.as_system_message()
    assert msg["role"] == "system"
    assert "diagnosis" in msg["content"]


def test_long_term_store_roundtrip(tmp_path):
    store = LongTermStore(db_path=str(tmp_path / "mem.db"))
    assert store.put("ns", "k1", {"a": 1}) is True
    assert store.get("ns", "k1") == {"a": 1}
    assert store.get("ns", "missing") is None


def test_long_term_diagnosis_history_capped(tmp_path):
    store = LongTermStore(db_path=str(tmp_path / "mem.db"))
    for i in range(10):
        store.push_student_diagnosis(2024001, {"i": i, "barrier": "concept"})
    history = store.student_diagnosis_history(2024001)
    assert len(history) == DIAGNOSIS_HISTORY_MAX_ITEMS
    assert history[-1]["i"] == 9


def test_memory_system_composes():
    ms = MemorySystem(long_term_store=LongTermStore(db_path=":memory:"))
    ms.working.add({"role": "user", "content": "hi"})
    ms.episodic.record("k", "v")
    ms.bind_student_profile({"name": "张三", "barrier": "concept"})
    assert ms.profile_system_message()["role"] == "system"
    assert "张三" in ms.profile_system_message()["content"]


# ---------- 上下文三层裁剪 ----------

def _mk(n):
    return [{"role": "user" if i % 2 == 0 else "assistant", "content": f"msg{i}"} for i in range(n)]


def test_trim_below_threshold_unchanged():
    msgs = _mk(30)
    trimmed, dropped = trim_context(msgs)
    assert len(trimmed) == 30
    assert dropped == []


def test_trim_over_threshold_keeps_recent_and_keyword():
    msgs = [{"role": "user", "content": f"诊断msg{i}"} for i in range(40)]
    trimmed, dropped = trim_context(msgs)
    # 关键词"诊断"命中全部 → 全保留
    assert len(trimmed) == 40
    assert dropped == []


def test_trim_drops_non_keyword_older():
    msgs = _mk(40)  # msg0..msg39 不含关键词
    trimmed, dropped = trim_context(msgs)
    assert len(trimmed) == KEEP_RECENT
    assert len(dropped) == 34


def test_keyword_hit():
    assert keyword_hit({"role": "user", "content": "学生的诊断结果是概念障碍"}) is True
    assert keyword_hit({"role": "user", "content": "你好呀"}) is False


def test_summary_only_when_enough_dropped():
    dropped = _mk(10)
    import asyncio

    async def _fake_summarizer(d):
        return "摘要内容"

    assert asyncio.run(compress_summary(dropped, _fake_summarizer)) == "摘要内容"


def test_summary_skipped_when_few_dropped():
    import asyncio
    assert asyncio.run(compress_summary(_mk(3), lambda d: "x")) is None


def test_summary_failure_drops():
    import asyncio

    async def _boom(dropped):
        raise RuntimeError("llm down")

    assert asyncio.run(compress_summary(_mk(10), _boom)) is None


def test_assemble_message_order():
    messages = assemble_messages(
        [{"role": "user", "content": "old"}],
        "current",
        profile_message={"role": "system", "content": "档案"},
        episodic_message={"role": "system", "content": "情景"},
        summary="摘要",
    )
    roles = [m["role"] for m in messages]
    assert roles == ["system", "system", "system", "user", "user"]
    assert messages[-1]["content"] == "current"


def test_context_manager_prepare():
    async def _run():
        cm = ContextManager(summarizer=lambda d: "摘要")
        result = await cm.prepare(_mk(35), "cur")
        return result

    import asyncio
    result = asyncio.run(_run())
    assert result["messages"][-1]["content"] == "cur"
