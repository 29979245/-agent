"""MCP 工具服务器单测（doc 30 十二 / spec「MCP 工具服务器」）。

- 注册表：16 工具齐全、参数 Schema、未注册名抛错、角色门控、参数校验。
- 端点：GET /api/mcp/tools 列表；POST /api/mcp/call 通用调用；POST /api/mcp/tools/{name}；
  未注册 404 / 越权 403 / 参数非法 422；未携带令牌 401。
"""
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.agents.mcp.registry import (
    MCPToolForbidden,
    MCPToolNotFound,
    call_mcp_tool,
    get_mcp_tool,
    list_mcp_tools,
)
from app.core.permissions import UserContext
from app.core.security import create_token
from app.db.session import get_db
from app.main import app

EXPECTED_TOOLS = {
    "ocr_recognize", "generate_questions", "generate_variant", "get_wrong_questions",
    "create_training", "submit_training", "get_review_tasks", "complete_review",
    "get_class_overview", "get_student_stats", "trigger_warning_check", "get_pending_warnings",
    "send_notification", "diagnose_question", "get_barrier_distribution", "get_knowledge_heatmap",
}


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


def _uc(role="teacher", user_id=1):
    return UserContext(user_id=user_id, role=role, school_id=1)


# ---------------------------------------------------------------- 注册表


def test_registry_lists_16_tools():
    tools = list_mcp_tools()
    assert len(tools) == 16
    names = {t["name"] for t in tools}
    assert names == EXPECTED_TOOLS
    for t in tools:
        assert t["description"]
        assert "parameters" in t  # pydantic JSON Schema


def test_registry_get_unknown_raises():
    with pytest.raises(MCPToolNotFound):
        get_mcp_tool("bogus")


def test_registry_call_get_review_tasks_empty(db_session):
    """真实 handler 调用：无到期任务返回空列表（确定性通过 services 层）。"""
    import asyncio

    result = asyncio.run(call_mcp_tool(
        "get_review_tasks", db=db_session, user=_uc("student"), arguments={"student_id": 1}
    ))
    assert result == {"student_id": 1, "count": 0, "items": []}


def test_registry_call_validation_error(db_session):
    import asyncio

    with pytest.raises(ValidationError):
        asyncio.run(call_mcp_tool(
            "get_review_tasks", db=db_session, user=_uc("student"), arguments={"student_id": "x"}
        ))


def test_registry_call_role_gate_blocks_student(db_session):
    import asyncio

    with pytest.raises(MCPToolForbidden):
        asyncio.run(call_mcp_tool(
            "trigger_warning_check", db=db_session, user=_uc("student"), arguments={}
        ))


def test_registry_call_role_gate_allows_teacher(db_session):
    import asyncio

    result = asyncio.run(call_mcp_tool(
        "trigger_warning_check", db=db_session, user=_uc("teacher"), arguments={}
    ))
    assert "total" in result  # EarlyWarningService summary


# ---------------------------------------------------------------- 端点


def test_list_endpoint_returns_16(client):
    resp = client.get("/api/mcp/tools", headers=_auth())
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 16
    assert {t["name"] for t in data["tools"]} == EXPECTED_TOOLS


def test_list_endpoint_requires_auth(client):
    resp = client.get("/api/mcp/tools")
    assert resp.status_code == 401


def test_call_endpoint_invokes_tool(client, monkeypatch):
    import app.api.v1.mcp as mcp_api

    async def _fake_call(name, *, db, user, arguments):
        return {"echo": name, "args": arguments}

    monkeypatch.setattr(mcp_api, "call_mcp_tool", _fake_call)
    resp = client.post(
        "/api/mcp/call",
        json={"tool": "get_review_tasks", "arguments": {"student_id": 7}},
        headers=_auth(role="student"),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["tool"] == "get_review_tasks"
    assert body["result"] == {"echo": "get_review_tasks", "args": {"student_id": 7}}


def test_call_endpoint_unknown_404(client, monkeypatch):
    import app.api.v1.mcp as mcp_api

    async def _raise(name, *, db, user, arguments):
        raise MCPToolNotFound(name)

    monkeypatch.setattr(mcp_api, "call_mcp_tool", _raise)
    resp = client.post(
        "/api/mcp/call", json={"tool": "bogus", "arguments": {}}, headers=_auth()
    )
    assert resp.status_code == 404
    assert resp.json()["error_code"] == "MCP_TOOL_NOT_FOUND"


def test_call_endpoint_forbidden_403(client, monkeypatch):
    import app.api.v1.mcp as mcp_api

    async def _raise(name, *, db, user, arguments):
        raise MCPToolForbidden("越权")

    monkeypatch.setattr(mcp_api, "call_mcp_tool", _raise)
    resp = client.post(
        "/api/mcp/call", json={"tool": "trigger_warning_check", "arguments": {}}, headers=_auth()
    )
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "MCP_TOOL_FORBIDDEN"


def test_tools_name_endpoint(client, monkeypatch):
    import app.api.v1.mcp as mcp_api

    async def _fake_call(name, *, db, user, arguments):
        return {"called": name}

    monkeypatch.setattr(mcp_api, "call_mcp_tool", _fake_call)
    resp = client.post(
        "/api/mcp/tools/get_review_tasks",
        json={"arguments": {"student_id": 7}},
        headers=_auth(role="student"),
    )
    assert resp.status_code == 200
    assert resp.json()["result"] == {"called": "get_review_tasks"}


def test_tools_name_endpoint_requires_auth(client):
    resp = client.post("/api/mcp/tools/get_review_tasks", json={"arguments": {}})
    assert resp.status_code == 401
