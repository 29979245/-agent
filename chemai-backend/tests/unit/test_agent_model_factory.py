"""LLM 模型工厂单测（tasks 1.1 / 审查补丁 11.1-11.2 熔断部分）。

覆盖：Provider 配置声明式能力标志、未识别 Provider 启动异常、三级回退、
每级 3 次指数退避、熔断器（连续失败 ≥3 熔断 30s、熔断期短路、冷却恢复）。
"""
import asyncio

import pytest
from langchain_core.messages import AIMessageChunk, ToolCallChunk
from langchain_core.outputs import ChatGenerationChunk
from langchain_core.tools import tool

from app.agents.factories.model_factory import (
    FALLBACK_CHAIN,
    CircuitBreaker,
    LLMClient,
    ProviderError,
    get_provider_config,
    provider_capability,
)


@tool
async def show_my_wrong_questions() -> dict:
    """列出当前登录学生的错题。适用：学生询问自己的错题。"""
    return {}


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


# ---------- 流式工具调用（astream_message 修复：bind_tools 委托丢工具 → 预格式化直传） ----------

class StreamingToolModel:
    """记录 `_astream` 收到的 kwargs，产出带 tool_call_chunks 的流式 chunk。

    bind_tools 本会被调用但修复后必须不再触发（其 RunnableBinding 属性委托会丢工具）。
    """

    def __init__(self, with_tool_calls: bool = True):
        self.bind_calls = 0
        self.astream_kwargs: dict | None = None
        self.with_tool_calls = with_tool_calls

    def bind_tools(self, tools, **kwargs):
        self.bind_calls += 1
        return self

    async def _astream(self, messages, **kwargs):
        self.astream_kwargs = kwargs
        if self.with_tool_calls:
            chunk = AIMessageChunk(
                content="",
                tool_call_chunks=[ToolCallChunk(name="show_my_wrong_questions", args="{}", id="call_1")],
            )
        else:
            chunk = AIMessageChunk(content="你好")
        yield ChatGenerationChunk(message=chunk)


def _streaming_client(model):
    return LLMClient(model_builder=lambda p: model, retries=1)


def test_astream_message_formats_tools_and_skips_bind_tools():
    """带工具：直接以预格式化 OpenAI dict 调 `_astream`，不再走 bind_tools。"""
    async def _run():
        model = StreamingToolModel()
        client = _streaming_client(model)
        chunks = [
            c async for c in client.astream_message(
                "deepseek", [{"role": "user", "content": "查看我的错题"}], tools=[show_my_wrong_questions]
            )
        ]
        return chunks, model

    chunks, model = asyncio.run(_run())
    assert model.bind_calls == 0  # 修复核心：不经 bind_tools（其 `_astream` 委托会丢绑定参数）
    tools = model.astream_kwargs.get("tools")
    assert isinstance(tools, list) and len(tools) == 1
    assert tools[0]["type"] == "function"
    assert tools[0]["function"]["name"] == "show_my_wrong_questions"
    assert chunks[0].tool_call_chunks
    assert chunks[0].tool_call_chunks[0]["name"] == "show_my_wrong_questions"


def test_astream_message_without_tools_calls_plain():
    """无工具：`_astream` 不携带 tools kwarg，文本 chunk 原样透出。"""
    async def _run():
        model = StreamingToolModel(with_tool_calls=False)
        client = _streaming_client(model)
        chunks = [
            c async for c in client.astream_message("deepseek", [{"role": "user", "content": "hi"}])
        ]
        return chunks, model

    chunks, model = asyncio.run(_run())
    assert "tools" not in model.astream_kwargs
    assert "".join(str(c.content) for c in chunks) == "你好"
