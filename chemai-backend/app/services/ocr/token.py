"""百度 OAuth Access Token 管理（设计 D4）。

模块级内存缓存 + 300s 安全边距：距到期 >300s 直接复用缓存，≤300s 或已过期
触发 client_credentials 刷新并更新缓存。async 单线程事件循环下天然线程安全。
凭据缺失（BAIDU_OCR_API_KEY / BAIDU_OCR_SECRET_KEY）报错并提示配置缺失。
"""
from __future__ import annotations

import time

import httpx

from app.config import settings

# 距到期 ≤ 此秒数即触发刷新（提前刷新避免到期竞态）
SAFETY_MARGIN = 300
TOKEN_URL = "https://aip.baidubce.com/oauth/2.0/token"

_cache: dict = {"token": None, "expires_at": 0.0}


class BaiduTokenError(RuntimeError):
    """百度 Token 获取失败：凭据缺失 / 响应异常 / 传输错误。"""


def reset_token_cache() -> None:
    """清空模块级缓存（供测试隔离）。"""
    _cache["token"] = None
    _cache["expires_at"] = 0.0


def _require_credentials() -> None:
    if not settings.baidu_ocr_api_key or not settings.baidu_ocr_secret_key:
        raise BaiduTokenError(
            "百度 OCR 凭据未配置（BAIDU_OCR_API_KEY / BAIDU_OCR_SECRET_KEY）"
        )


async def _request_token(client: httpx.AsyncClient) -> dict:
    resp = await client.get(
        TOKEN_URL,
        params={
            "grant_type": "client_credentials",
            "client_id": settings.baidu_ocr_api_key,
            "client_secret": settings.baidu_ocr_secret_key,
        },
    )
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, dict) or "access_token" not in data:
        raise BaiduTokenError(f"百度 Token 响应异常: {data}")
    return data


async def get_access_token(
    http: httpx.AsyncClient | None = None,
    now: float | None = None,
) -> str:
    """获取百度 OAuth Access Token（内存缓存 + 300s 安全边距）。

    - http 可注入（测试用 MockTransport），None 时内部创建并关闭临时客户端；
    - now 可注入（测试控制时间），None 时取当前时间。
    """
    _require_credentials()
    now = now if now is not None else time.time()
    cached = _cache["token"]
    if cached and (_cache["expires_at"] - now) > SAFETY_MARGIN:
        return cached
    if http is not None:
        data = await _request_token(http)
    else:
        async with httpx.AsyncClient() as client:
            data = await _request_token(client)
    _cache["token"] = data["access_token"]
    _cache["expires_at"] = now + float(data.get("expires_in", 0))
    return _cache["token"]
