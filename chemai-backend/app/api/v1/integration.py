"""LMS/Webhook 集成 API（design D9/D10，挂载于 /api/integration）。

§1 Webhook：注册/列表(secret 掩码)/取消/手动触发。§2 LMS：配置存取 + 同步状态占位。
全部端点 admin+（integration 资源在 RBAC 矩阵，parent 等其余角色 default-deny）。
"""
from fastapi import APIRouter, Body, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.exceptions import BusinessRuleViolationError
from app.core.permissions import require_permission
from app.db.models import IntegrationConfig, WebhookRegistration
from app.db.models.enums import WebhookEventType
from app.db.session import get_db
from app.services.integration.webhook_service import trigger_event

integration_router = APIRouter()

UNSUPPORTED_MSG = "不支持的事件类型"


def _parse_event_type(raw: str) -> WebhookEventType:
    """解析事件类型字符串；不支持返回 400（业务规则冲突）。"""
    try:
        return WebhookEventType(raw)
    except ValueError:
        raise BusinessRuleViolationError(
            detail=f"不支持的事件类型: {raw}",
            error_code="UNSUPPORTED_EVENT_TYPE",
            suggestion="请使用 7 种受支持的事件类型",
        )


def _mask_secret(secret: str) -> str:
    return secret[:4] + "****" if len(secret) > 4 else "****"


def _registration_dict(reg: WebhookRegistration) -> dict:
    return {
        "id": reg.id,
        "event_type": reg.event_type.value,
        "url": reg.url,
        "secret": _mask_secret(reg.secret),
        "enabled": reg.enabled,
        "created_at": reg.created_at.isoformat() if reg.created_at else None,
        "last_delivery_status": reg.last_delivery_status,
    }


# ---------------- §1 Webhook ----------------

class WebhookRegisterRequest(BaseModel):
    event_type: str
    url: str = Field(min_length=1, max_length=512)
    secret: str = Field(min_length=1, max_length=256)


@integration_router.get("/webhooks")
@require_permission("integration", "read")
def list_webhooks(request: Request, db: Session = Depends(get_db)) -> dict:
    rows = db.query(WebhookRegistration).order_by(WebhookRegistration.id).all()
    return {"webhooks": [_registration_dict(r) for r in rows]}


@integration_router.post("/webhooks")
@require_permission("integration", "create")
def register_webhook(
    request: Request,
    payload: WebhookRegisterRequest,
    db: Session = Depends(get_db),
) -> dict:
    event_type = _parse_event_type(payload.event_type)
    reg = WebhookRegistration(
        event_type=event_type,
        url=payload.url,
        secret=payload.secret,
        enabled=True,
    )
    db.add(reg)
    db.commit()
    return _registration_dict(reg)


@integration_router.delete("/webhooks/{event_type}")
@require_permission("integration", "delete")
def cancel_webhook(
    request: Request,
    event_type: str,
    db: Session = Depends(get_db),
) -> dict:
    parsed = _parse_event_type(event_type)
    removed = (
        db.query(WebhookRegistration)
        .filter(WebhookRegistration.event_type == parsed)
        .delete()
    )
    db.commit()
    return {"detail": "已取消注册", "event_type": parsed.value, "removed": removed}


@integration_router.post("/webhooks/trigger/{event_type}")
@require_permission("integration", "update")
def trigger_webhook(
    request: Request,
    event_type: str,
    db: Session = Depends(get_db),
) -> dict:
    parsed = _parse_event_type(event_type)
    # 请求路径内不 sleep：backoff_base=0（真实投递失败由调度/手动重试兜底）
    return trigger_event(db, parsed, {"event_type": parsed.value}, backoff_base=0.0)


# ---------------- §2 LMS 配置与同步 ----------------

LMS_CONFIG_KEY = "lms"


def _lms_config_row(db: Session) -> IntegrationConfig | None:
    return db.query(IntegrationConfig).filter(IntegrationConfig.key == LMS_CONFIG_KEY).first()


@integration_router.get("/lms/config")
@require_permission("integration", "read")
def get_lms_config(request: Request, db: Session = Depends(get_db)) -> dict:
    row = _lms_config_row(db)
    return {"config": row.value if row else {}}


@integration_router.post("/lms/config")
@require_permission("integration", "update")
def save_lms_config(
    request: Request,
    config: dict = Body(...),
    db: Session = Depends(get_db),
) -> dict:
    row = _lms_config_row(db)
    if row is None:
        row = IntegrationConfig(key=LMS_CONFIG_KEY, value={})
        db.add(row)
    row.value = config
    db.commit()
    return {"config": row.value}


@integration_router.post("/lms/sync")
@require_permission("integration", "update")
def lms_sync(request: Request, db: Session = Depends(get_db)) -> dict:
    """手动触发数据同步；外部连接器均未启用时返回状态占位而非报错。"""
    row = _lms_config_row(db)
    config = row.value if row else {}
    connectors = [k for k, v in config.items() if k in ("dingtalk", "wecom", "lti", "sftp") and v]
    if not connectors:
        return {
            "status": "no_connector_enabled",
            "message": "未启用外部连接器，本次同步未执行",
            "connectors": [],
        }
    return {"status": "ok", "message": "同步已触发", "connectors": connectors}
