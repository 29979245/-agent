"""模型工厂（doc 30 §14 / doc 38 §2 / design D10/D12）。

- PROVIDER_CONFIG：三级 Provider（MiMo-V2.5 → qwen-turbo → DeepSeek）的模型名/base_url/密钥，
  以 `capabilities` 声明式标志描述能力（D12：stream_tool_args/vision/web_search，不做运行时探测）。
- CircuitBreaker：进程内熔断器（D10）——每 Provider 连续失败 ≥3 次熔断 30s，熔断期请求短路下一级。
- LLMClient：统一 ChatOpenAI 兼容接口（temperature=0.3, max_tokens=4096），
  链内每级 3 次重试 + 指数退避（1s→2s→4s），429/5xx 判定可重试。
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, AsyncIterator, Callable, Optional

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.utils.function_calling import convert_to_openai_tool
from langchain_openai import ChatOpenAI

from app.config import settings

logger = logging.getLogger(__name__)

# 三级 Fallback 链（与 app/services/diagnosis/llm_diagnosis.PROVIDER_CHAIN 一致，ADR-0008）
FALLBACK_CHAIN: tuple[str, ...] = ("mimo", "qwen", "deepseek")

BREAKER_FAILURE_THRESHOLD = 3
BREAKER_COOLDOWN_SECONDS = 30.0
RETRY_PER_PROVIDER = 3
RETRY_BACKOFF_SECONDS = (1, 2, 4)

# 可重试异常判据：openai SDK 的 RateLimitError / APIConnectionError / APIStatusError(5xx)
RETRYABLE_STATUS_CODES = {429, 500, 502, 503}


def _provider_api_key(provider: str) -> str:
    if provider == "mimo":
        return settings.mimo_api_key
    if provider == "qwen":
        return settings.qwen_api_key
    if provider == "deepseek":
        return settings.deepseek_api_key
    return ""


def _provider_base_url(provider: str) -> str:
    if provider == "mimo":
        return settings.mimo_base_url
    if provider == "qwen":
        return settings.qwen_base_url
    if provider == "deepseek":
        return settings.deepseek_base_url
    return ""


def get_provider_config(provider: str) -> dict:
    """返回 Provider 配置字典；未识别 Provider 触发启动异常（doc 30 §16）。"""
    if provider not in FALLBACK_CHAIN:
        raise ValueError(f"未识别的 LLM Provider: {provider}（允许: {', '.join(FALLBACK_CHAIN)}）")
    base = {
        "mimo": ("MiMo-V2.5", {"vision": True, "web_search": True, "stream_tool_args": True}),
        "qwen": ("qwen-turbo", {"vision": False, "web_search": False, "stream_tool_args": True}),
        "deepseek": ("deepseek-chat", {"vision": False, "web_search": False, "stream_tool_args": True}),
    }
    model, capabilities = base[provider]
    return {
        "name": provider,
        "model": model,
        "base_url": _provider_base_url(provider),
        "api_key": _provider_api_key(provider),
        "capabilities": capabilities,
    }


def provider_capability(provider: str, key: str, default: bool = False) -> bool:
    """读取 Provider 声明式能力标志（D12）。"""
    return get_provider_config(provider).get("capabilities", {}).get(key, default)


class CircuitBreaker:
    """进程内熔断器（D10）：连续失败 ≥threshold 次熔断 cooldown 秒，冷却后半开自动恢复。"""

    def __init__(self, threshold: int = BREAKER_FAILURE_THRESHOLD,
                 cooldown: float = BREAKER_COOLDOWN_SECONDS) -> None:
        self.threshold = threshold
        self.cooldown = cooldown
        self._failures = 0
        self._opened_at = 0.0

    def allow(self) -> bool:
        if self._opened_at == 0.0:
            return True
        if time.monotonic() - self._opened_at >= self.cooldown:
            self._reset()  # 半开：放行试探
            return True
        return False

    def record_success(self) -> None:
        self._reset()

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self.threshold:
            self._opened_at = time.monotonic()

    def _reset(self) -> None:
        self._failures = 0
        self._opened_at = 0.0

    @property
    def open(self) -> bool:
        return self._opened_at != 0.0 and time.monotonic() - self._opened_at < self.cooldown


class LLMClient:
    """三级回退 LLM 客户端（doc 30 §14.3）。

    model_builder 可注入（测试传 FakeModel）：`(provider) -> ChatOpenAI|FakeModel`。
    """

    def __init__(
        self,
        chain: tuple[str, ...] = FALLBACK_CHAIN,
        retries: int = RETRY_PER_PROVIDER,
        backoff: tuple[float, ...] = RETRY_BACKOFF_SECONDS,
        temperature: float = 0.3,
        max_tokens: int = 4096,
        model_builder: Optional[Callable[[str], ChatOpenAI]] = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.chain = chain
        self.retries = retries
        self.backoff = backoff
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._model_builder = model_builder or self._default_model_builder
        self._sleep = sleep
        self._breakers: dict[str, CircuitBreaker] = {p: CircuitBreaker() for p in chain}
        self._models: dict[str, ChatOpenAI] = {}

    # ---------- 模型构建 ----------
    def _default_model_builder(self, provider: str) -> ChatOpenAI:
        cfg = get_provider_config(provider)
        if not cfg["api_key"]:
            raise RuntimeError(f"LLM Provider '{provider}' 未配置 API key（{provider.upper()}_API_KEY）")
        if provider == "mimo" and not cfg["base_url"]:
            raise RuntimeError("LLM Provider 'mimo' 未配置 MIMO_BASE_URL")
        return ChatOpenAI(
            model=cfg["model"],
            base_url=cfg["base_url"],
            api_key=cfg["api_key"],
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            streaming=True,
            timeout=60,
        )

    def get_model(self, provider: str) -> ChatOpenAI:
        if provider not in self._models:
            self._models[provider] = self._model_builder(provider)
        return self._models[provider]

    # ---------- 回退 + 熔断 ----------
    def _is_retryable(self, exc: BaseException) -> bool:
        status = getattr(exc, "status_code", None)
        if status is not None and isinstance(status, int):
            return status in RETRYABLE_STATUS_CODES
        # openai SDK：RateLimitError / APIConnectionError 可重试；AuthenticationError 等不可
        name = type(exc).__name__
        return name in {"RateLimitError", "APIConnectionError", "APITimeoutError",
                        "InternalServerError", "ServiceUnavailableError"}

    def ainvoke(self, provider: str, messages: list[dict]) -> str:
        """单 Provider 同步调用（经指数退避重试）；最终失败抛 ProviderError。"""
        breaker = self._breakers[provider]
        if not breaker.allow():
            raise ProviderError(provider, f"熔断中（{type(self).__name__}）", status_code=503)
        last_exc: BaseException | None = None
        for attempt in range(self.retries):
            try:
                model = self.get_model(provider)
                result = model.invoke(messages)
                breaker.record_success()
                return _extract_text(result)
            except Exception as exc:  # noqa: BLE001 —— 统一走重试判定
                last_exc = exc
                if not self._is_retryable(exc):
                    break
                if attempt < self.retries - 1:
                    self._sleep(self.backoff[min(attempt, len(self.backoff) - 1)])
        breaker.record_failure()
        raise ProviderError(provider, str(last_exc), status_code=_status_of(last_exc))

    async def acomplete(self, provider: str, messages: list[dict]) -> str:
        """异步单 Provider 调用；供回退循环在子协程中驱动。"""
        breaker = self._breakers[provider]
        if not breaker.allow():
            raise ProviderError(provider, f"熔断中（{provider}）", status_code=503)
        last_exc: BaseException | None = None
        for attempt in range(self.retries):
            try:
                model = self.get_model(provider)
                if hasattr(model, "ainvoke"):
                    result = await model.ainvoke(messages)
                else:
                    result = model.invoke(messages)
                breaker.record_success()
                return _extract_text(result)
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if not self._is_retryable(exc):
                    break
                if attempt < self.retries - 1:
                    await asyncio.sleep(self.backoff[min(attempt, len(self.backoff) - 1)])
        breaker.record_failure()
        raise ProviderError(provider, str(last_exc), status_code=_status_of(last_exc))

    async def acomplete_chain(self, messages: list[dict]) -> str:
        """按链依次尝试，熔断/耗尽自动切下一级；全部失败抛 ProviderError。"""
        last_err: ProviderError | None = None
        for provider in self.chain:
            if not self._breakers[provider].allow():
                continue
            try:
                return await self.acomplete(provider, messages)
            except ProviderError as e:
                last_err = e
                logger.warning("[LLM] provider=%s 失败（%s），切换下级", provider, e)
        raise ProviderError("all", f"三级 Fallback 全部失败: {last_err}", status_code=503)

    def complete_chain(self, messages: list[dict]) -> str:
        """同步链式调用（供既有同步服务如周报/诊断的 .complete() 接口复用）。

        不创建/驱动事件循环，可在 async 上下文中安全调用（内部走 ainvoke 同步分支）。
        """
        last_err: ProviderError | None = None
        for provider in self.chain:
            if not self._breakers[provider].allow():
                continue
            try:
                return self.ainvoke(provider, messages)
            except ProviderError as e:
                last_err = e
                logger.warning("[LLM] provider=%s 同步失败（%s），切换下级", provider, e)
        raise ProviderError("all", f"三级 Fallback 全部失败: {last_err}", status_code=503)

    async def astream_chain(self, messages: list[dict]) -> AsyncIterator[str]:
        """链式流式文本：逐 token 产出；Provider 失败时按链重试（重放输入）。"""
        last_err: ProviderError | None = None
        for provider in self.chain:
            if not self._breakers[provider].allow():
                continue
            model = self.get_model(provider)
            try:
                if hasattr(model, "astream"):
                    async for chunk in model.astream(messages):
                        text = _chunk_text(chunk)
                        if text:
                            yield text
                    self._breakers[provider].record_success()
                    return
                raise ProviderError(provider, "模型不支持 astream", status_code=501)
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                logger.warning("[LLM] stream provider=%s 失败（%s），切换下级", provider, exc)
        raise ProviderError("all", f"三级 Fallback 流式全部失败: {last_err}", status_code=503)

    # ---------- 完整消息调用（ReAct 工具调用需要保留 tool_calls，doc 38 §3） ----------
    async def ainvoke_message(self, provider: str, messages: list, tools=None):
        """单 Provider 异步调用，返回完整 AIMessage（含 tool_calls）。经熔断 + 退避重试。"""
        breaker = self._breakers[provider]
        if not breaker.allow():
            raise ProviderError(provider, f"熔断中（{provider}）", status_code=503)
        last_exc: BaseException | None = None
        for attempt in range(self.retries):
            try:
                model = self.get_model(provider)
                if tools:
                    model = model.bind_tools(tools)
                result = await model.ainvoke(messages)
                breaker.record_success()
                return result
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if not self._is_retryable(exc):
                    break
                if attempt < self.retries - 1:
                    await asyncio.sleep(self.backoff[min(attempt, len(self.backoff) - 1)])
        breaker.record_failure()
        raise ProviderError(provider, str(last_exc), status_code=_status_of(last_exc))

    async def astream_message(self, provider: str, messages: list, tools=None) -> AsyncIterator:
        """单 Provider 流式调用，逐 chunk 产出 AIMessageChunk（含 tool_call_chunks）。

        经熔断 + 退避重试。走底层 `_astream` 而非 Runnable `astream`：避免内层模型
        （ChatOpenAI）作为子 Runnable 再次向 astream_events 冒泡，导致 SSE 事件重复
        （外层 FallbackChatModel 已是唯一模型 Runnable）。

        工具以预格式化 OpenAI dict 直接传给 `_astream`：先 `bind_tools` 再 `_astream`
        时，RunnableBinding 的属性委托丢弃绑定参数（tools 不随流式请求发送，模型把
        工具调用写成文本并编造数据）；`_astream` 也不接收原始 StructuredTool（400）。
        """
        breaker = self._breakers[provider]
        if not breaker.allow():
            raise ProviderError(provider, f"熔断中（{provider}）", status_code=503)
        last_exc: BaseException | None = None
        for attempt in range(self.retries):
            try:
                model = self.get_model(provider)
                if tools:
                    stream = model._astream(messages, tools=[convert_to_openai_tool(t) for t in tools])
                else:
                    stream = model._astream(messages)
                async for cg_chunk in stream:
                    # _astream 产出 ChatGenerationChunk；解包为 AIMessageChunk 供 SSE 适配器
                    yield cg_chunk.message if hasattr(cg_chunk, "message") else cg_chunk
                breaker.record_success()
                return
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if not self._is_retryable(exc):
                    raise ProviderError(provider, str(exc), status_code=_status_of(exc))
                if attempt < self.retries - 1:
                    await asyncio.sleep(self.backoff[min(attempt, len(self.backoff) - 1)])
        breaker.record_failure()
        raise ProviderError(provider, str(last_exc), status_code=_status_of(last_exc))


class FallbackChatModel(BaseChatModel):
    """把 LLMClient 三级回退包装为 LangChain 可绑定工具的聊天模型（doc 38 §3）。

    create_react_agent 需要 BaseChatModel（.bind_tools / .ainvoke / .astream）。
    本类按链尝试 Provider，失败自动切下一级；保留 AIMessage 的 tool_calls 与
    流式 tool_call_chunks（SSE tool_args 依赖，D12）。
    """

    llm: Any
    tools: Any = None  # bind_tools 时注入的工具列表

    @property
    def _llm_type(self) -> str:
        return "fallback-chat-model"

    def bind_tools(self, tools, **kwargs) -> "FallbackChatModel":
        return FallbackChatModel(llm=self.llm, tools=list(tools))

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        last_err: ProviderError | None = None
        for provider in self.llm.chain:
            if not self.llm._breakers[provider].allow():
                continue
            try:
                msg = await self.llm.ainvoke_message(provider, messages, tools=self.tools)
                return ChatResult(generations=[ChatGeneration(message=msg)])
            except ProviderError as e:
                last_err = e
                logger.warning("[LLM] agent provider=%s 失败（%s），切换下级", provider, e)
        raise ProviderError("all", f"ReAct 三级 Fallback 全部失败: {last_err}", status_code=503)

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs) -> AsyncIterator:
        last_err: ProviderError | None = None
        for provider in self.llm.chain:
            if not self.llm._breakers[provider].allow():
                continue
            try:
                # BaseChatModel.astream 契约：_astream 须产出 ChatGenerationChunk（含 .message）
                async for chunk in self.llm.astream_message(provider, messages, tools=self.tools):
                    yield ChatGenerationChunk(message=chunk)
                return
            except ProviderError as e:
                last_err = e
                logger.warning("[LLM] agent stream provider=%s 失败（%s），切换下级", provider, e)
        raise ProviderError("all", f"ReAct 三级 Fallback 流式全部失败: {last_err}", status_code=503)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        raise RuntimeError("FallbackChatModel 仅支持异步调用（ReAct 走 ainvoke/astream）")


FallbackChatModel.model_rebuild()  # pydantic v2：Any 字段需重建以解析懒引用


class ProviderError(RuntimeError):
    """Provider 调用失败（doc 30 §11.1）。status_code 判定可重试性。"""

    def __init__(self, provider: str, message: str, status_code: int | None = None) -> None:
        super().__init__(f"provider={provider}: {message}")
        self.provider = provider
        self.status_code = status_code
        self.code = "PROVIDER_ERROR"


def _extract_text(result) -> str:
    """从 model.invoke/ainvoke 返回值提取文本（AIMessage / 字典 / 字符串）。"""
    content = getattr(result, "content", None)
    if content is not None:
        return content if isinstance(content, str) else "".join(str(c) for c in content)
    if isinstance(result, dict):
        return str(result.get("content", ""))
    return str(result)


def _chunk_text(chunk) -> str:
    if hasattr(chunk, "content"):
        content = chunk.content
        if isinstance(content, str):
            return content
    text = getattr(chunk, "text", None)
    return text or ""


def _status_of(exc: BaseException | None) -> int | None:
    if exc is None:
        return None
    status = getattr(exc, "status_code", None)
    return status if isinstance(status, int) else None
