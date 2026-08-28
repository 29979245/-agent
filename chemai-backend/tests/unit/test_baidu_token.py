"""百度 Token 管理单元测试（task 3.1）：缓存复用 / 安全边际刷新 / 凭据缺失。

覆盖：缓存命中（>300s 边距不发起请求）、安全边际内刷新（≤300s 触发 client_credentials）、
过期刷新、凭据缺失报错、刷新后缓存更新。httpx 走 MockTransport，无真实凭据。
"""
import httpx
import pytest

from app.config import settings
from app.services.ocr.token import (
    BaiduTokenError,
    SAFETY_MARGIN,
    get_access_token,
    reset_token_cache,
)

API_KEY = "ak_test"
SECRET_KEY = "sk_test"
BASE = 1_000_000.0  # 基准时间戳


@pytest.fixture(autouse=True)
def _credentials_and_cleanup(monkeypatch):
    monkeypatch.setattr(settings, "baidu_ocr_api_key", API_KEY)
    monkeypatch.setattr(settings, "baidu_ocr_secret_key", SECRET_KEY)
    reset_token_cache()
    yield
    reset_token_cache()


def _token_response(token: str, expires_in: int) -> httpx.Response:
    return httpx.Response(200, json={"access_token": token, "expires_in": expires_in})


def _client(calls: list, responses: list[httpx.Response]):
    def handler(request: httpx.Request):
        calls.append(request)
        return responses.pop(0)

    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://aip.baidubce.com",
    )


@pytest.mark.asyncio
async def test_cache_reuse_within_safe_margin():
    """缓存令牌距到期 >300s → 直接复用，不发起第二次请求。"""
    calls: list[httpx.Request] = []
    async with _client(calls, [_token_response("t1", 2592000)]) as client:
        first = await get_access_token(http=client, now=BASE)
        # 前进 1000s，距到期仍远大于 300s
        second = await get_access_token(http=client, now=BASE + 1000)
        assert first == second == "t1"
        assert len(calls) == 1


@pytest.mark.asyncio
async def test_refresh_within_safety_margin():
    """距到期 ≤300s → 触发刷新。"""
    calls: list[httpx.Request] = []
    responses = [_token_response("t1", 500), _token_response("t2", 500)]
    async with _client(calls, responses) as client:
        await get_access_token(http=client, now=BASE)
        # expires_at = BASE+500；BASE+300 处剩余 200 ≤300 → 刷新
        refreshed = await get_access_token(http=client, now=BASE + 300)
        assert refreshed == "t2"
        assert len(calls) == 2
        assert all("client_credentials" in str(c.url) for c in calls)


@pytest.mark.asyncio
async def test_refresh_when_expired():
    """已过期 → 触发刷新。"""
    calls: list[httpx.Request] = []
    responses = [_token_response("t1", 500), _token_response("t2", 500)]
    async with _client(calls, responses) as client:
        await get_access_token(http=client, now=BASE)
        refreshed = await get_access_token(http=client, now=BASE + 600)
        assert refreshed == "t2"
        assert len(calls) == 2


@pytest.mark.asyncio
async def test_cache_updated_after_refresh():
    """刷新后新令牌与到期时间写入缓存，安全期内复用新令牌。"""
    calls: list[httpx.Request] = []
    responses = [_token_response("t1", 500), _token_response("t2", 500)]
    async with _client(calls, responses) as client:
        await get_access_token(http=client, now=BASE)
        refreshed = await get_access_token(http=client, now=BASE + 300)  # 刷新到 t2
        still = await get_access_token(http=client, now=BASE + 400)       # 距 t2 到期 400>300 复用
        assert refreshed == still == "t2"
        assert len(calls) == 2


@pytest.mark.asyncio
async def test_missing_credentials_raises(monkeypatch):
    """凭据缺失 → BaiduTokenError，提示配置缺失，不发起请求。"""
    monkeypatch.setattr(settings, "baidu_ocr_api_key", "")
    with pytest.raises(BaiduTokenError) as exc:
        await get_access_token(now=BASE)
    assert "配置" in str(exc.value)


@pytest.mark.asyncio
async def test_bad_response_raises():
    """Token 响应缺 access_token → 报错。"""
    calls: list[httpx.Request] = []
    bad = httpx.Response(200, json={"error": "invalid_client"})
    async with _client(calls, [bad]) as client:
        with pytest.raises(BaiduTokenError):
            await get_access_token(http=client, now=BASE)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_safety_margin_value():
    assert SAFETY_MARGIN == 300
