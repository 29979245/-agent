"""change parent-backend 3.3/3.4：Webhook API + LMS 配置/同步 API。

覆盖：注册 / 列表(secret 掩码) / 不支持类型 400 / 取消 / 手动触发 / 非 admin 403；
LMS 配置存取 / 无连接器同步不报错。
"""
import pytest
from fastapi.testclient import TestClient

from app.core.security import create_token
from app.db.models import WebhookRegistration
from app.db.models.enums import WebhookEventType
from app.db.session import get_db
from app.main import app


def _admin():
    return {"Authorization": f"Bearer {create_token(user_id=1, role='admin')}"}


def _teacher():
    return {"Authorization": f"Bearer {create_token(user_id=2, role='teacher')}"}


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


def _reg(db_session, event_type=WebhookEventType.warning_triggered):
    reg = WebhookRegistration(
        event_type=event_type,
        url="https://school.example.com/hook",
        secret="super-secret-value",
        enabled=True,
    )
    db_session.add(reg)
    db_session.commit()
    return reg


# ---------------- Webhook 注册 / 列表 / 取消 ----------------

def test_register_webhook_secret_masked(client, db_session):
    resp = client.post(
        "/api/integration/webhooks",
        json={"event_type": "warning.triggered", "url": "https://x/hook", "secret": "super-secret-value"},
        headers=_admin(),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] is not None
    assert body["event_type"] == "warning.triggered"
    assert "super-secret-value" not in body["secret"]
    assert body["secret"].endswith("****")
    row = db_session.get(WebhookRegistration, body["id"])
    assert row.secret == "super-secret-value"  # 明文入库（投递签名必需），API 不回显


def test_register_unsupported_event_type_400(client, db_session):
    resp = client.post(
        "/api/integration/webhooks",
        json={"event_type": "bogus.type", "url": "https://x", "secret": "s"},
        headers=_admin(),
    )
    assert resp.status_code == 400
    assert resp.json()["error_code"] == "UNSUPPORTED_EVENT_TYPE"
    assert db_session.query(WebhookRegistration).count() == 0


def test_list_webhooks_secret_masked(client, db_session):
    _reg(db_session)
    resp = client.get("/api/integration/webhooks", headers=_admin())
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["webhooks"]) == 1
    serialized = str(body)
    assert "super-secret-value" not in serialized
    assert body["webhooks"][0]["secret"].endswith("****")


def test_cancel_webhook_by_event_type(client, db_session):
    _reg(db_session)
    _reg(db_session, event_type=WebhookEventType.exam_created)
    resp = client.delete("/api/integration/webhooks/warning.triggered", headers=_admin())
    assert resp.status_code == 200
    assert resp.json()["removed"] == 1
    assert db_session.query(WebhookRegistration).count() == 1


def test_cancel_unsupported_event_type_400(client, db_session):
    resp = client.delete("/api/integration/webhooks/bogus.type", headers=_admin())
    assert resp.status_code == 400
    assert resp.json()["error_code"] == "UNSUPPORTED_EVENT_TYPE"


# ---------------- 手动触发 ----------------

class _FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code


def test_manual_trigger_delivers(client, db_session, monkeypatch):
    _reg(db_session)
    calls = []

    def fake_post(url, data=None, headers=None, timeout=None):
        calls.append({"url": url, "data": data, "headers": headers})
        return _FakeResponse(200)

    monkeypatch.setattr("app.services.integration.webhook_service.requests.post", fake_post)
    resp = client.post("/api/integration/webhooks/trigger/warning.triggered", headers=_admin())
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["deliveries"][0]["status"] == "delivered"
    assert len(calls) == 1
    assert calls[0]["headers"]["X-Webhook-Signature"]
    assert calls[0]["headers"]["X-Webhook-Timestamp"]


def test_manual_trigger_no_registration(client, db_session):
    resp = client.post("/api/integration/webhooks/trigger/exam.graded", headers=_admin())
    assert resp.status_code == 200
    assert resp.json()["total"] == 0


def test_manual_trigger_unsupported_400(client, db_session):
    resp = client.post("/api/integration/webhooks/trigger/bogus.type", headers=_admin())
    assert resp.status_code == 400


# ---------------- 权限 ----------------

def test_webhooks_non_admin_403(client, db_session):
    _reg(db_session)
    resp = client.get("/api/integration/webhooks", headers=_teacher())
    assert resp.status_code == 403


def test_webhooks_missing_token_401(client, db_session):
    resp = client.get("/api/integration/webhooks")
    assert resp.status_code == 401


# ---------------- LMS 配置与同步 ----------------

def test_lms_config_default_empty(client, db_session):
    resp = client.get("/api/integration/lms/config", headers=_admin())
    assert resp.status_code == 200
    assert resp.json()["config"] == {}


def test_lms_config_save_and_read(client, db_session):
    resp = client.post(
        "/api/integration/lms/config",
        json={"enabled": True, "dingtalk": True, "signature": {"algorithm": "HMAC-SHA256"}},
        headers=_admin(),
    )
    assert resp.status_code == 200
    assert resp.json()["config"]["enabled"] is True
    resp2 = client.get("/api/integration/lms/config", headers=_admin())
    assert resp2.json()["config"]["dingtalk"] is True


def test_lms_sync_no_connector(client, db_session):
    resp = client.post("/api/integration/lms/config", json={"enabled": False}, headers=_admin())
    assert resp.status_code == 200
    resp2 = client.post("/api/integration/lms/sync", headers=_admin())
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "no_connector_enabled"


def test_lms_sync_with_connector(client, db_session):
    client.post(
        "/api/integration/lms/config",
        json={"enabled": True, "dingtalk": True},
        headers=_admin(),
    )
    resp = client.post("/api/integration/lms/sync", headers=_admin())
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert resp.json()["connectors"] == ["dingtalk"]
