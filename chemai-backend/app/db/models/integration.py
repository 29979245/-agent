"""LMS/Webhook 集成链模型：WebhookRegistration + IntegrationConfig（文档 33 §10）。

secret 明文入库（投递 HMAC 签名必需），API 层掩码回显、不回传明文。
"""
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.enums import DbEnum, WebhookEventType


class WebhookRegistration(Base):
    """Webhook 注册：事件类型 → 回调 URL + 签名密钥。同一事件类型可多条注册。"""

    __tablename__ = "webhook_registration"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_type: Mapped[WebhookEventType] = mapped_column(
        DbEnum(WebhookEventType), nullable=False, index=True
    )
    url: Mapped[str] = mapped_column(String(512), nullable=False)
    secret: Mapped[str] = mapped_column(String(256), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    last_delivery_status: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    last_delivered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class IntegrationConfig(Base):
    """集成配置单例键值表：LMS 连接器启用标志 + 签名设置等（JSON 值）。"""

    __tablename__ = "integration_config"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    value: Mapped[dict] = mapped_column(
        MutableDict.as_mutable(JSON), nullable=False, default=dict
    )
