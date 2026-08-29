"""审批恢复流程单测（doc 30 §5.2 第 4 层 / spec 审批流程 / design D13，tasks 6.1）。

- _approval_instruction：approve 指示 LLM 复调工具，reject 指示调整行为。
- /api/agent/approval/resume：pending→确认从检查点恢复、取消收到拒绝；无 pending → 409；
  decision 非法 → 422；thread_id 含 ":" → 403（D14）。
- run_agent_chat resume：decision=approve 预标记 Guard L4 → 审批工具放行执行；
  无 resume → awaiting_approval 暂停（不发 done）。
"""
import asyncio

import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, ToolCallChunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult

from app.agents.agent import (
    get_pending_approval,
    pop_pending_approval,
    run_agent_chat,
    store_pending_approval,
    thread_key,
)
from app.agents.factories.model_factory import LLMClient
from app.core.permissions import UserContext
from app.core.security import create_token
from app.db.session import get_db
from app.main import app


@pytest.fixture()
def client(db_session):
    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db

    def _teardown():
        app.dependency_overrides.pop(get_db, None)

    with TestClient(app) as c:
        yield c
    _teardown()


def _auth(role="teacher", user_id=1):
    return {"Authorization": f"Bearer {create_token(user_id=user_id, role=role, school_id=1)}"}


# ---------------------------------------------------------------- 审批指令文本

def test_approval_instruction_approve():
    from app.api.v1.agent import _approval_instruction
    msg = _approval_instruction({"tool": "delete_bank", "args": {"bank_id": 3}}, "approve")
    assert "delete_bank" in msg
    assert "确认" in msg


def test_approval_instruction_reject():
    from app.api.v1.agent import _approval_instruction
    msg = _approval_instruction({"tool": "delete_bank", "args": {"bank_id": 3}}, "reject")
    assert "拒绝" in msg
    assert "停止" in msg


# ---------------------------------------------------------------- 恢复端点

def _seed_pending(user_id=1, thread_id="t1", **extra):
    key = thread_key(user_id, thread_id)
    info = {"tool": "delete_bank", "args": {"bank_id": 3}, "thread_id": thread_id,
            "persona": "teacher", **extra}
    store_pending_approval(key, info)
    return key


def test_resume_approve_streams(client, monkeypatch):
    import app.api.v1.agent as agent_api

    _seed_pending()
    captured = {}

    async def _capture(**kwargs):
        captured.update(kwargs)
        yield "done", {"type": "done"}

    monkeypatch.setattr(agent_api, "run_agent_chat", _capture)
    resp = client.post(
        "/api/agent/approval/resume",
        json={"thread_id": "t1", "decision": "approve"},
        headers=_auth(),
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert "event: done" in resp.text
    # 恢复指令注入为 message，resume 携带 decision/tool/args
    assert "已确认执行工具 delete_bank" in captured["message"]
    assert captured["resume"] == {"tool": "delete_bank", "args": {"bank_id": 3}, "decision": "approve"}
    # 恢复后注册表清空
    assert get_pending_approval(thread_key(1, "t1")) is None


def test_resume_reject_streams(client, monkeypatch):
    import app.api.v1.agent as agent_api

    _seed_pending()
    captured = {}

    async def _capture(**kwargs):
        captured.update(kwargs)
        yield "done", {"type": "done"}

    monkeypatch.setattr(agent_api, "run_agent_chat", _capture)
    resp = client.post(
        "/api/agent/approval/resume",
        json={"thread_id": "t1", "decision": "reject"},
        headers=_auth(),
    )
    assert resp.status_code == 200
    assert "已拒绝执行工具 delete_bank" in captured["message"]
    assert captured["resume"]["decision"] == "reject"


def test_resume_no_pending_409(client):
    resp = client.post(
        "/api/agent/approval/resume",
        json={"thread_id": "t1", "decision": "approve"},
        headers=_auth(),
    )
    assert resp.status_code == 409
    assert resp.json()["error_code"] == "NO_PENDING_APPROVAL"


def test_resume_bad_decision_422(client):
    _seed_pending()
    resp = client.post(
        "/api/agent/approval/resume",
        json={"thread_id": "t1", "decision": "maybe"},
        headers=_auth(),
    )
    assert resp.status_code == 422


def test_resume_ownership_separator_403(client):
    resp = client.post(
        "/api/agent/approval/resume",
        json={"thread_id": "other:thread", "decision": "approve"},
        headers=_auth(),
    )
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "THREAD_OWNERSHIP_DENIED"


# ---------------------------------------------------------------- run_agent_chat resume


class ApprovalToolModel(BaseChatModel):
    """脚本化假模型：第 1 次调用返回审批工具 delete_bank，之后返回文本。"""

    calls: int = 0

    @property
    def _llm_type(self):
        return "fake"

    def _next(self):
        self.calls += 1
        if self.calls == 1:
            return AIMessage(content="", tool_calls=[
                {"name": "delete_bank", "args": {"bank_id": 3}, "id": "c1", "type": "tool_call"}
            ])
        return AIMessage(content="题库已删除")

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        return ChatResult(generations=[ChatGeneration(message=self._next())])

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        msg = self._next()
        if msg.tool_calls:
            yield ChatGenerationChunk(message=AIMessageChunk(content="", tool_call_chunks=[
                ToolCallChunk(index=0, name="delete_bank", args='{"bank_id": 3}', id="c1")
            ]))
        else:
            yield ChatGenerationChunk(message=AIMessageChunk(content="题库已删除"))

    def bind_tools(self, tools, **kwargs):
        return self


def _client(model) -> LLMClient:
    return LLMClient(model_builder=lambda provider: model)


def _cp(tmp_path):
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    return AsyncSqliteSaver.from_conn_string(str(tmp_path / "cp.db"))


def test_run_agent_chat_without_resume_pauses_for_approval(tmp_path):
    async def _run():
        frames = []
        async for f in run_agent_chat(
            persona="teacher",
            user=UserContext(user_id=7, role="teacher"),
            thread_id="t7",
            message="请删除题库",
            llm_client=_client(ApprovalToolModel()),
            checkpointer_factory=lambda: _cp(tmp_path),
        ):
            frames.append(f)
        return frames

    frames = asyncio.run(_run())
    names = [n for n, _ in frames]
    # 暂停：awaiting_approval 阶段后不发 done
    assert "done" not in names
    phases = [p["content"] for n, p in frames if n == "phase"]
    assert "awaiting_approval" in phases
    # 注册表记录待审批
    assert get_pending_approval(thread_key(7, "t7")) is not None
    pop_pending_approval(thread_key(7, "t7"))


def test_run_agent_chat_resume_approve_executes_tool(tmp_path):
    async def _run():
        frames = []
        async for f in run_agent_chat(
            persona="teacher",
            user=UserContext(user_id=8, role="teacher"),
            thread_id="t8",
            message="用户已确认执行工具 delete_bank，参数：{'bank_id': 3}。请立即调用该工具完成操作。",
            llm_client=_client(ApprovalToolModel()),
            checkpointer_factory=lambda: _cp(tmp_path),
            resume={"tool": "delete_bank", "args": {"bank_id": 3}, "decision": "approve"},
        ):
            frames.append(f)
        return frames

    frames = asyncio.run(_run())
    names = [n for n, _ in frames]
    # 恢复后走完：done 收尾
    assert names[-1] == "done"
    tr = next(p for n, p in frames if n == "tool_result")
    assert tr["tool"] == "delete_bank"
    # 非阻塞：result 不含 requires_approval（delete_bank 无 db → not_found 属正常工具结果）
    assert "requires_approval" not in str(tr["result"])
