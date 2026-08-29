"""LLM 模型工厂单测（tasks 1.1 / 审查补丁 11.1-11.2 熔断部分）。

覆盖：Provider 配置声明式能力标志、未识别 Provider 启动异常、三级回退、
每级 3 次指数退避、熔断器（连续失败 ≥3 熔断 30s、熔断期短路、冷却恢复）。
"""
import asyncio

import pytest

from app.agents.factories.model_factory import (
    FALLBACK_CHAIN,
    CircuitBreaker,
    LLMClient,
    ProviderError,
    get_provider_config,
    provider_capability,
)


class FakeResult:
    def __init__(self, content: str = "ok"):
        self.content = content


class FakeAPIError(RuntimeError):
    """带 status_code 的假 HTTP 错误（模拟 openai SDK 异常，供重试判定）。"""

    def __init__(self, status: int):
        super().__init__(f"http {status}")
        self.status_code = status


class FakeModel:
    """行为按 provider 名区分的假模型：调用计数 + 可注入失败。"""

    def __init__(self, provider: str, fail_status: int | None = None, fail_after: int | None = None):
        self.provider = provider
        self.fail_status = fail_status
        self.fail_after = fail_after
        self.calls = 0

    def _maybe_fail(self):
        self.calls += 1
        if self.fail_after is not None:
            # fail_after=N：前 N 次失败，之后成功
            if self.calls <= self.fail_after:
                raise FakeAPIError(self.fail_status or 429)
            return self
        if self.fail_status is not None:
            raise FakeAPIError(self.fail_status)
        return self

    def invoke(self, messages):
        self._maybe_fail()
        return FakeResult(f"{self.provider}-ok")

    async def ainvoke(self, messages):
        return self.invoke(messages)

    async def astream(self, messages):
        self._maybe_fail()
        for token in [f"{self.provider}-t1", f"{self.provider}-t2"]:
            yield FakeResult(token)


def _client(fakes: dict, **kwargs):
    return LLMClient(model_builder=lambda p: fakes[p], sleep=lambda _s: None, **kwargs)


# ---------- Provider 配置 ----------

def test_provider_config_declares_capabilities():
    cfg = get_provider_config("mimo")
    assert cfg["capabilities"]["stream_tool_args"] is True
    assert cfg["capabilities"]["vision"] is True
    assert provider_capability("qwen", "stream_tool_args") is True
    assert provider_capability("qwen", "vision") is False


def test_unknown_provider_raises():
    with pytest.raises(ValueError, match="未识别的 LLM Provider"):
        get_provider_config("claude")


# ---------- 三级回退 + 熔断 ----------

def test_fallback_switches_to_next_provider():
    async def _run():
        fakes = {
            "mimo": FakeModel("mimo", fail_status=503),
            "qwen": FakeModel("qwen", fail_status=429),
            "deepseek": FakeModel("deepseek"),
        }
        client = _client(fakes)
        text = await client.acomplete_chain([{"role": "user", "content": "hi"}])
        return text, fakes["mimo"].calls, fakes["qwen"].calls

    text, mimo_calls, qwen_calls = asyncio.run(_run())
    assert text == "deepseek-ok"
    assert mimo_calls > 0
    assert qwen_calls > 0


def test_retries_per_provider_with_backoff():
    fakes = {"mimo": FakeModel("mimo", fail_status=429, fail_after=2)}
    client = _client(fakes, retries=3)
    text = client.ainvoke("mimo", [])
    assert text == "mimo-ok"
    assert fakes["mimo"].calls == 3  # 失败 2 次后第 3 次成功


def test_all_providers_fail_raises_provider_error():
    fakes = {
        "mimo": FakeModel("mimo", fail_status=503),
        "qwen": FakeModel("qwen", fail_status=503),
        "deepseek": FakeModel("deepseek", fail_status=503),
    }
    client = _client(fakes)
    with pytest.raises(ProviderError):
        client.ainvoke("mimo", [])


def test_breaker_opens_after_three_failures():
    breaker = CircuitBreaker(threshold=3, cooldown=30.0)
    for _ in range(3):
        breaker.record_failure()
    assert breaker.open is True
    assert breaker.allow() is False  # 熔断期短路


def test_breaker_shortcircuits_provider_in_chain():
    async def _run():
        fakes = {
            "mimo": FakeModel("mimo", fail_status=503),
            "qwen": FakeModel("qwen"),
        }
        client = _client(fakes, retries=1)
        # 让 mimo 连续失败 3 次触发熔断
        for _ in range(3):
            try:
                await client.acomplete("mimo", [])
            except ProviderError:
                pass
        assert client._breakers["mimo"].open is True
        # 熔断期链式调用直接跳过 mimo，走 qwen
        return await client.acomplete_chain([{"role": "user", "content": "hi"}])

    assert asyncio.run(_run()) == "qwen-ok"


def test_breaker_recovers_after_cooldown(monkeypatch):
    import time as _time

    real_monotonic = _time.monotonic
    breaker = CircuitBreaker(threshold=1, cooldown=30.0)
    breaker.record_failure()
    assert breaker.open is True
    monkeypatch.setattr(_time, "monotonic", lambda: real_monotonic() + 31)
    assert breaker.allow() is True  # 冷却后半开放行
    breaker.record_success()
    assert breaker.open is False


def test_breaker_success_resets_failures():
    breaker = CircuitBreaker(threshold=3)
    breaker.record_failure()
    breaker.record_failure()
    breaker.record_success()
    breaker.record_failure()
    assert breaker.open is False  # 未达 3 次连续失败


# ---------- 异步链式 ----------

def test_acomplete_chain():
    async def _run():
        fakes = {
            "mimo": FakeModel("mimo", fail_status=429),
            "qwen": FakeModel("qwen"),
        }
        client = _client(fakes, retries=1)
        return await client.acomplete_chain([{"role": "user", "content": "hi"}])

    assert asyncio.run(_run()) == "qwen-ok"


def test_astream_chain_streams_text():
    async def _run():
        fakes = {"mimo": FakeModel("mimo"), "qwen": FakeModel("qwen")}
        client = _client(fakes)
        tokens = [tok async for tok in client.astream_chain([{"role": "user", "content": "hi"}])]
        return "".join(tokens)

    assert asyncio.run(_run()) == "mimo-t1mimo-t2"


def test_missing_api_key_raises_config_error(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "deepseek_api_key", "")
    client = LLMClient(chain=("deepseek",), retries=1)
    with pytest.raises(RuntimeError, match="未配置 API key"):
        client.get_model("deepseek")
