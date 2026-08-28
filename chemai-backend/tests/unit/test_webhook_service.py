"""change parent-backend 3.2：Webhook 签名投递服务。

覆盖：签名向量锁定（规范化/时间戳拼接/HMAC） / 全启用注册投递 / 无注册 / 重试行为 /
重试耗尽落库 / disabled 跳过 / emit 吞异常不阻断。
"""
import json

import pytest

from app.db.models import WebhookRegistration
from app.db.models.enums import WebhookEventType
from app.services.integration.webhook_service import (
    canonical_json,
    emit_event,
    sign,
    signing_string,
    trigger_event,
    verify_signature,
)

# ---------------- 签名向量（锁定协议） ----------------

def test_signature_vector_locked():
    payload = {"name": "张三", "student_id": 7, "warning_type": "score_drop"}
    body = canonical_json(payload)
    assert body == '{"name":"张三","student_id":7,"warning_type":"score_drop"}'
    ts = "1724803200"
    assert signing_string(ts, body) == '1724803200.{"name":"张三","student_id":7,"warning_type":"score_drop"}'
    assert sign("secret-key-123", ts, body) == (
        "b5bd92391e6af27f3f7b83bcd00ba7be0f6fe17d8e0eac8650f61d4162be16eb"
    )


def test_verify_signature():
    body = '{"student_id":7}'
    ts = "1724803200"
    sig = sign("s", ts, body)
    assert verify_signature("s", ts, body, sig) is True
    assert verify_signature("wrong", ts, body, sig) is False
    assert verify_signature("s", ts, body, "") is False
    assert verify_signature("s", ts, body, sig[:-1] + ("0" if sig[-1] != "0" else "1")) is False


# ---------------- 投递行为 ----------------

class _FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code


def _reg(db_session, event_type=WebhookEventType.warning_triggered, enabled=True):
    reg = WebhookRegistration(
        event_type=event_type,
        url="https://school.example.com/hook",
        secret="secret-key-123",
        enabled=enabled,
    )
    db_session.add(reg)
    db_session.commit()
    return reg


def test_trigger_delivers_to_all_enabled_with_headers(db_session, monkeypatch):
    calls = []
    _reg(db_session)
    _reg(db_session)

    def fake_post(url, data=None, headers=None, timeout=None):
        calls.append({"url": url, "data": data, "headers": headers})
        return _FakeResponse(200)

    monkeypatch.setattr("app.services.integration.webhook_service.requests.post", fake_post)
    payload = {"student_id": 7, "name": "张三", "warning_type": "score_drop"}

    summary = trigger_event(db_session, WebhookEventType.warning_triggered, payload, timestamp="1724803200", backoff_base=0)
    assert summary["total"] == 2
    assert all(d["status"] == "delivered" for d in summary["deliveries"])
    assert len(calls) == 2
    for c in calls:
        assert c["url"] == "https://school.example.com/hook"
        assert c["data"].decode("utf-8") == '{"name":"张三","student_id":7,"warning_type":"score_drop"}'
        assert c["headers"]["X-Webhook-Timestamp"] == "1724803200"
        assert c["headers"]["X-Webhook-Signature"] == sign("secret-key-123", "1724803200", canonical_json(payload))


def test_trigger_no_registrations_no_delivery(db_session, monkeypatch):
    called = []
    monkeypatch.setattr(
        "app.services.integration.webhook_service.requests.post",
        lambda *a, **k: called.append(1) or _FakeResponse(200),
    )
    summary = trigger_event(db_session, WebhookEventType.exam_created, {"x": 1}, backoff_base=0)
    assert summary["total"] == 0
    assert summary["deliveries"] == []
    assert called == []


def test_trigger_disabled_registration_skipped(db_session, monkeypatch):
    _reg(db_session, enabled=False)
    called = []
    monkeypatch.setattr(
        "app.services.integration.webhook_service.requests.post",
        lambda *a, **k: called.append(1) or _FakeResponse(200),
    )
    summary = trigger_event(db_session, WebhookEventType.warning_triggered, {"x": 1}, backoff_base=0)
    assert summary["total"] == 0
    assert called == []


def test_trigger_retries_then_succeeds(db_session, monkeypatch):
    _reg(db_session)
    statuses = iter([_FakeResponse(500), _FakeResponse(500), _FakeResponse(200)])
    attempts = []

    def fake_post(url, data=None, headers=None, timeout=None):
        attempts.append(1)
        return next(statuses)

    monkeypatch.setattr("app.services.integration.webhook_service.requests.post", fake_post)
    summary = trigger_event(db_session, WebhookEventType.warning_triggered, {"x": 1}, backoff_base=0)
    assert len(attempts) == 3  # 失败 2 次 + 成功 1 次
    assert summary["deliveries"][0]["status"] == "delivered"


def test_trigger_exhausted_records_failed(db_session, monkeypatch):
    reg = _reg(db_session)
    monkeypatch.setattr(
        "app.services.integration.webhook_service.requests.post",
        lambda *a, **k: _FakeResponse(503),
    )
    summary = trigger_event(db_session, WebhookEventType.warning_triggered, {"x": 1}, backoff_base=0)
    assert len(summary["deliveries"]) == 1
    assert summary["deliveries"][0]["status"] == "failed"
    db_session.refresh(reg)
    assert reg.last_delivery_status == "failed"
    assert reg.last_delivered_at is not None


def test_trigger_network_error_retries(db_session, monkeypatch):
    _reg(db_session)
    import requests as _requests
    attempts = []

    def fake_post(*a, **k):
        attempts.append(1)
        raise _requests.ConnectionError("boom")

    monkeypatch.setattr("app.services.integration.webhook_service.requests.post", fake_post)
    summary = trigger_event(db_session, WebhookEventType.warning_triggered, {"x": 1}, backoff_base=0)
    assert len(attempts) == 3
    assert summary["deliveries"][0]["status"] == "failed"


def test_emit_event_swallows_exception(db_session, monkeypatch):
    import requests as _requests

    def boom(*a, **k):
        raise _requests.ConnectionError("offline")

    monkeypatch.setattr("app.services.integration.webhook_service.requests.post", boom)
    # emit 必须吞异常，不抛、不阻断业务
    assert emit_event(db_session, WebhookEventType.practice_assigned, {"student_id": 1}) is None
