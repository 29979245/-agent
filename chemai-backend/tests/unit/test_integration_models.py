"""change parent-backend 3.1：Webhook 注册 + 集成配置模型。

覆盖：字段齐全 / 事件类型枚举 7 类约束 / 非法事件类型被 DB 拒绝 / config 键唯一。
"""
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.db.models import IntegrationConfig, WebhookRegistration
from app.db.models.enums import WebhookEventType


def test_webhook_event_type_has_seven_members():
    values = {m.value for m in WebhookEventType}
    assert values == {
        "practice.assigned",
        "practice.completed",
        "exam.created",
        "exam.graded",
        "warning.triggered",
        "student.login",
        "review.due",
    }


def test_webhook_registration_fields(db_session):
    reg = WebhookRegistration(
        event_type=WebhookEventType.warning_triggered,
        url="https://school.example.com/hook",
        secret="s3cret",
        enabled=True,
    )
    db_session.add(reg)
    db_session.commit()
    db_session.refresh(reg)
    assert reg.id is not None
    assert reg.event_type == WebhookEventType.warning_triggered
    assert reg.url == "https://school.example.com/hook"
    assert reg.secret == "s3cret"
    assert reg.enabled is True
    assert reg.created_at is not None


def test_webhook_registration_invalid_event_type_rejected(db_session):
    # 枚举以 VARCHAR + CHECK 落地，非法取值在 DB 层拒绝
    reg = WebhookRegistration(
        event_type=WebhookEventType.student_login,
        url="https://x/hook",
        secret="s",
    )
    db_session.add(reg)
    db_session.commit()
    with pytest.raises(IntegrityError):
        db_session.execute(
            text(
                "INSERT INTO webhook_registration (event_type, url, secret, enabled, created_at) "
                "VALUES ('bogus.type', 'https://x', 's', 1, '2026-08-28 00:00:00')"
            )
        )


def test_integration_config_upsert_by_key(db_session):
    cfg = IntegrationConfig(key="lms", value={"enabled": False, "dingtalk": False})
    db_session.add(cfg)
    db_session.commit()
    db_session.refresh(cfg)
    assert cfg.value == {"enabled": False, "dingtalk": False}


def test_integration_config_key_unique(db_session):
    db_session.add(IntegrationConfig(key="lms", value={"enabled": True}))
    db_session.commit()
    db_session.add(IntegrationConfig(key="lms", value={"enabled": False}))
    with pytest.raises(IntegrityError):
        db_session.commit()
