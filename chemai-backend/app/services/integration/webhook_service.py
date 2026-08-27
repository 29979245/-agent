"""Webhook 签名投递服务（文档 33 §10.3 / design D9）。

签名协议：规范化载荷（键排序 + 紧凑 JSON）为请求体；签名串 = 时间戳 + "." + 请求体，
HMAC-SHA256（secret 为注册私钥）→ `X-Webhook-Signature`；时间戳随 `X-Webhook-Timestamp`
头携带，供接收方验证来源与防重放。投递失败指数退避重试 ≤3，结果写回注册行；
业务模块 emit 经 emit_event 吞异常不阻断主流程。
"""
import datetime
import hashlib
import hmac
import json
import logging
import time

import requests
from sqlalchemy.orm import Session

from app.db.models import WebhookRegistration
from app.db.models.enums import WebhookEventType

logger = logging.getLogger(__name__)

RETRY_ATTEMPTS = 3
BACKOFF_BASE = 1.0  # 秒：第 n 次重试前 sleep 2^(n-1)
TIMEOUT = 5.0


class WebhookServiceError(RuntimeError):
    """Webhook 投递服务不可用（签名/注册等自身异常）。"""


def canonical_json(payload: dict) -> str:
    """规范化载荷：键递归排序 + 紧凑 JSON。签名与投递共用同一文本，保证可复现。"""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def signing_string(timestamp: str, body: str) -> str:
    return f"{timestamp}.{body}"


def sign(secret: str, timestamp: str, body: str) -> str:
    """HMAC-SHA256 十六进制签名。"""
    return hmac.new(
        secret.encode("utf-8"),
        signing_string(timestamp, body).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def verify_signature(secret: str, timestamp: str, body: str, signature: str) -> bool:
    """接收方校验：常数时间比较。"""
    if not signature:
        return False
    return hmac.compare_digest(sign(secret, timestamp, body), signature)


def _enabled_registrations(db: Session, event_type: WebhookEventType) -> list[WebhookRegistration]:
    return (
        db.query(WebhookRegistration)
        .filter(
            WebhookRegistration.event_type == event_type,
            WebhookRegistration.enabled.is_(True),
        )
        .all()
    )


def _do_post(
    reg: WebhookRegistration, body: str, timestamp: str, signature: str
) -> tuple[bool, int | None, str]:
    """单次投递；返回 (成功?, HTTP状态, 错误信息)。不抛网络异常。"""
    try:
        resp = requests.post(
            reg.url,
            data=body.encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "X-Webhook-Signature": signature,
                "X-Webhook-Timestamp": timestamp,
            },
            timeout=TIMEOUT,
        )
        if resp.status_code < 400:
            return True, resp.status_code, ""
        return False, resp.status_code, f"HTTP {resp.status_code}"
    except requests.RequestException as e:  # 超时/连接失败等
        return False, None, str(e)


def _deliver_to_registration(
    db: Session,
    reg: WebhookRegistration,
    body: str,
    timestamp: str,
    backoff_base: float = BACKOFF_BASE,
) -> dict:
    """对单条注册投递：指数退避重试 ≤3，结果写回注册行（结果落库）。"""
    signature = sign(reg.secret, timestamp, body)
    last_error = "delivery failed"
    last_http = None
    for attempt in range(RETRY_ATTEMPTS):
        ok, http_status, error = _do_post(reg, body, timestamp, signature)
        if ok:
            reg.last_delivery_status = "delivered"
            reg.last_delivered_at = datetime.datetime.utcnow()
            db.commit()
            return {"webhook_id": reg.id, "status": "delivered", "http_status": http_status}
        last_error = error or "delivery failed"
        last_http = http_status
        if attempt < RETRY_ATTEMPTS - 1:
            time.sleep(backoff_base * (2**attempt))
    reg.last_delivery_status = "failed"
    reg.last_delivered_at = datetime.datetime.utcnow()
    db.commit()
    return {
        "webhook_id": reg.id,
        "status": "failed",
        "http_status": last_http,
        "error": last_error,
    }


def trigger_event(
    db: Session,
    event_type: WebhookEventType,
    payload: dict,
    timestamp: str | None = None,
    backoff_base: float = BACKOFF_BASE,
) -> dict:
    """触发事件：向该类型全部启用注册投递签名载荷，返回投递结果汇总。

    无启用注册时返回空 deliveries，不产生任何投递（事件处理正常返回）。
    """
    registrations = _enabled_registrations(db, event_type)
    timestamp = timestamp or str(int(time.time()))
    body = canonical_json(payload)
    deliveries = [
        _deliver_to_registration(db, reg, body, timestamp, backoff_base=backoff_base)
        for reg in registrations
    ]
    return {
        "event_type": event_type.value,
        "total": len(deliveries),
        "deliveries": deliveries,
    }


def emit_event(db: Session, event_type: WebhookEventType, payload: dict) -> None:
    """业务埋点入口：吞掉一切异常，绝不阻断主流程（无注册/投递失败只记日志）。"""
    try:
        trigger_event(db, event_type, payload, backoff_base=0.0)
    except Exception as e:  # noqa: BLE001 - 埋点必须不阻塞业务
        logger.warning("[Webhook] emit %s 失败：%s", event_type.value, e)
