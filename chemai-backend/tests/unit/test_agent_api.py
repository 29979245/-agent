"""Agent SSE 端点测试（doc 30 §13 / design D12/D14，tasks 对话闭环）。

- 未携带令牌 → 401（/api/agent 已从中间件白名单移除）。
- 已认证 → text/event-stream，帧格式 `event:/data:`，以 done 收尾。
- version="v1" → 501（AgentVersionError 映射）。
- thread_id 含 ":" → 403（D14 所有权守卫）。
- 缺 message → 422。
- persona 默认映射（role=student → student）。
"""
import pytest
from fastapi.testclient import TestClient

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


def _auth(role="student", user_id=1):
    return {"Authorization": f"Bearer {create_token(user_id=user_id, role=role, school_id=1)}"}


def _body(**overrides):
    payload = {"message": "你好", "persona": "", "thread_id": "t1", "version": "v2", "context": {}}
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------- 401

def test_stream_requires_auth(client):
    resp = client.post("/api/agent/chat/langgraph/stream", json=_body())
    assert resp.status_code == 401
    assert resp.json()["error_code"] == "AUTHENTICATION_REQUIRED"


# ---------------------------------------------------------------- 认证流

async def _fake_run_agent_chat(**kwargs):
    yield "phase", {"type": "phase", "content": "thinking"}
    yield "text", {"type": "text", "content": "你好，我是化学助手"}
    yield "done", {"type": "done"}


async def _fake_classify_chat(*args, **kwargs):
    """Gateway 分类桩：一律 chat（避免测试触发真实 LLM 调用）。"""
    from app.agents.gateway import IntentResult
    return IntentResult(type="chat", tools=[], source="keyword")


def _patch_classify(monkeypatch):
    import app.api.v1.agent as agent_api
    monkeypatch.setattr(agent_api, "classify_intent", _fake_classify_chat)


def test_stream_returns_sse_frames(client, monkeypatch):
    import app.api.v1.agent as agent_api

    _patch_classify(monkeypatch)
    monkeypatch.setattr(agent_api, "run_agent_chat", _fake_run_agent_chat)
    resp = client.post("/api/agent/chat/langgraph/stream", json=_body(), headers=_auth())
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    text = resp.text
    assert "event: phase" in text
    assert "event: text" in text
    assert "event: done" in text
    assert "data: {\"type\": \"phase\", \"content\": \"thinking\"}" in text
    # 以 done 收尾
    assert text.rstrip().endswith('event: done\ndata: {"type": "done"}')


def test_stream_default_persona_by_role(client, monkeypatch):
    import app.api.v1.agent as agent_api

    captured = {}

    async def _capture(**kwargs):
        captured.update(kwargs)
        yield "done", {"type": "done"}

    _patch_classify(monkeypatch)
    monkeypatch.setattr(agent_api, "run_agent_chat", _capture)
    # role=teacher，未指定 persona → 默认 teacher
    resp = client.post("/api/agent/chat/langgraph/stream", json=_body(), headers=_auth(role="teacher"))
    assert resp.status_code == 200
    assert captured["persona"] == "teacher"
    assert captured["thread_id"] == "t1"
    assert captured["message"] == "你好"
    assert captured["user"].role == "teacher"


def test_stream_context_persona_override(client, monkeypatch):
    import app.api.v1.agent as agent_api

    captured = {}

    async def _capture(**kwargs):
        captured.update(kwargs)
        yield "done", {"type": "done"}

    _patch_classify(monkeypatch)
    monkeypatch.setattr(agent_api, "run_agent_chat", _capture)
    body = _body(persona="", context={"persona": "parent"})
    resp = client.post("/api/agent/chat/langgraph/stream", json=body, headers=_auth(role="teacher"))
    assert resp.status_code == 200
    assert captured["persona"] == "parent"


# ---------------------------------------------------------------- version 门 / 所有权

def test_stream_v1_version_501(client):
    resp = client.post(
        "/api/agent/chat/langgraph/stream",
        json=_body(version="v1"),
        headers=_auth(),
    )
    assert resp.status_code == 501
    assert resp.json()["error_code"] == "AGENT_VERSION_UNSUPPORTED"


def test_stream_thread_id_separator_403(client):
    resp = client.post(
        "/api/agent/chat/langgraph/stream",
        json=_body(thread_id="other:thread"),
        headers=_auth(),
    )
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "THREAD_OWNERSHIP_DENIED"


def test_stream_context_user_id_mismatch_403(client):
    resp = client.post(
        "/api/agent/chat/langgraph/stream",
        json=_body(context={"user_id": 999}),  # JWT 主体为 1
        headers=_auth(user_id=1),
    )
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "THREAD_OWNERSHIP_DENIED"


def test_stream_context_user_id_match_passes(client, monkeypatch):
    import app.api.v1.agent as agent_api

    _patch_classify(monkeypatch)
    monkeypatch.setattr(agent_api, "run_agent_chat", _fake_run_agent_chat)
    resp = client.post(
        "/api/agent/chat/langgraph/stream",
        json=_body(context={"user_id": 1}),  # 与 JWT 主体一致 → 放行
        headers=_auth(user_id=1),
    )
    assert resp.status_code == 200
    assert "event: done" in resp.text


def test_stream_missing_message_422(client):
    resp = client.post(
        "/api/agent/chat/langgraph/stream",
        json={"thread_id": "t1"},
        headers=_auth(),
    )
    assert resp.status_code == 422


def test_stream_persona_error_yields_error_done(client, monkeypatch):
    import app.api.v1.agent as agent_api

    async def _boom(**kwargs):
        raise ValueError("bad persona")
        yield  # 使函数成为 async generator，启动迭代时抛错

    _patch_classify(monkeypatch)
    monkeypatch.setattr(agent_api, "run_agent_chat", _boom)
    resp = client.post("/api/agent/chat/langgraph/stream", json=_body(), headers=_auth())
    assert resp.status_code == 200  # 流式：错误以事件承载
    text = resp.text
    assert "event: error" in text
    assert "event: done" in text


# ---------------------------------------------------------------- Gateway 接线 / 限流

async def _fake_classify_navigate(*args, **kwargs):
    from app.agents.gateway import PAGE_EXAM, IntentResult
    return IntentResult(type="navigate", page=PAGE_EXAM, source="keyword")


def test_stream_navigate_shortcut_skips_react(client, monkeypatch):
    import app.api.v1.agent as agent_api

    called = {"react": False}

    async def _should_not_run(**kwargs):
        called["react"] = True
        yield "done", {"type": "done"}

    monkeypatch.setattr(agent_api, "classify_intent", _fake_classify_navigate)
    monkeypatch.setattr(agent_api, "run_agent_chat", _should_not_run)
    resp = client.post("/api/agent/chat/langgraph/stream", json=_body(), headers=_auth())
    assert resp.status_code == 200
    text = resp.text
    assert "event: navigate" in text
    assert "data: {\"type\": \"navigate\", \"page\": \"exam-v2\"" in text
    assert "event: done" in text
    assert not called["react"], "navigate 快捷路径不应触发 ReAct"


def test_stream_rate_limit_429(client, monkeypatch):
    import app.api.v1.agent as agent_api
    from app.core.ratelimit import TokenBucketLimiter

    _patch_classify(monkeypatch)
    # 容量 1、补充 0 → 第二次请求必然 429
    monkeypatch.setattr(agent_api, "agent_limiter", TokenBucketLimiter(capacity=1, refill_rate=0.0))
    headers = _auth()
    first = client.post("/api/agent/chat/langgraph/stream", json=_body(), headers=headers)
    assert first.status_code == 200
    second = client.post("/api/agent/chat/langgraph/stream", json=_body(), headers=headers)
    assert second.status_code == 429
    assert second.json()["error_code"] == "RATE_LIMIT_EXCEEDED"
