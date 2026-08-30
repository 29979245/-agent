"""浏览器工具组（doc 30 §3.8）：Playwright 单实例 + 60s 空闲回收。

5 个工具：browse_navigate / browse_read / browse_click / browse_input / browse_screenshot。
浏览器不可用（未安装 Playwright 浏览器/启动失败）时各工具返回结构化消息而非抛错，
与 web_search 的降级约定一致，LLM 可据此告知用户。
"""
from __future__ import annotations

import base64
import functools
import time
from typing import Any, Callable, Optional

from pydantic import BaseModel, Field

from app.agents.tools.context import ToolContext

IDLE_TIMEOUT_S = 60.0
MAX_TEXT = 8000


class BrowseNavigateArgs(BaseModel):
    url: str = Field(..., description="目标 URL")
    wait: Optional[str] = Field(default=None, description="等待策略（networkidle/domcontentloaded 等，可选）")


class BrowseReadArgs(BaseModel):
    selector: str = Field(..., description="元素选择器")


class BrowseClickArgs(BaseModel):
    selector: str = Field(..., description="元素选择器")


class BrowseInputArgs(BaseModel):
    selector: str = Field(..., description="元素选择器")
    text: str = Field(..., description="输入文本")


class BrowseScreenshotArgs(BaseModel):
    selector: Optional[str] = Field(default=None, description="区域选择器（可选，缺省整页）")


class BrowserSession:
    """单浏览器实例，60s 无操作空闲超时自动回收（doc 30 §3.8）。"""

    _instance: Optional["BrowserSession"] = None

    def __init__(self) -> None:
        self._playwright: Any = None
        self._browser: Any = None
        self._page: Any = None
        self._last_used: float = 0.0

    @classmethod
    def get(cls) -> "BrowserSession":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def page(self) -> Any:
        """返回当前页面实例；空闲超时先回收再重启。"""
        idle = time.monotonic() - self._last_used
        if self._browser is not None and idle > IDLE_TIMEOUT_S:
            await self.close()
        if self._browser is None:
            self._page = await self._start()
        self._last_used = time.monotonic()
        return self._page

    async def _start(self) -> Any:
        """惰性启动无头 Chromium（独立方法便于测试 patch，避免强依赖 playwright 安装）。"""
        from playwright.async_api import async_playwright

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=True)
        return await self._browser.new_page()

    async def close(self) -> None:
        for obj in (self._browser, self._playwright):
            if obj is not None:
                try:
                    await obj.close()
                except Exception:  # noqa: BLE001 —— 回收失败静默
                    pass
        self._browser = self._playwright = self._page = None

    async def dispose(self) -> None:
        """测试收尾专用：关闭并重置单例。"""
        await self.close()
        BrowserSession._instance = None


async def _browser_unavailable() -> dict:
    return {"error": "browser_unavailable",
            "message": "浏览器服务不可用（需安装 Playwright 浏览器：playwright install chromium）",
            "_guard_error": True}  # 与兄弟工具错误 dict 形状一致（doc 30 §5.3）


def _degrade(func: Callable[..., Any]) -> Callable[..., Any]:
    """浏览器启动/操作失败统一降级：捕获任意异常返回结构化错误（doc 30 §3.8 降级约定）。

    避免 5 个工具各自重复 try/except + _browser_unavailable 包装。
    """
    @functools.wraps(func)
    async def wrapper(ctx: ToolContext, **kwargs: Any) -> dict:
        try:
            return await func(ctx, **kwargs)
        except Exception:  # noqa: BLE001 —— 与既有工具降级一致
            return await _browser_unavailable()
    return wrapper


@_degrade
async def browse_navigate(ctx: ToolContext, url: str, wait: Optional[str] = None) -> dict:
    """打开 URL 等待加载完成，返回页面标题和文本（上限 8000 字，doc 30 §3.8）。"""
    page = await BrowserSession.get().page()
    await page.goto(url, wait_until=wait or "load", timeout=30000)
    title = await page.title()
    text = (await page.inner_text("body"))[:MAX_TEXT]
    return {"url": url, "title": title, "text": text}


@_degrade
async def browse_read(ctx: ToolContext, selector: str) -> dict:
    """提取指定页面元素的文本内容（上限 8000 字符）。"""
    page = await BrowserSession.get().page()
    text = (await page.inner_text(selector))[:MAX_TEXT]
    return {"selector": selector, "text": text}


@_degrade
async def browse_click(ctx: ToolContext, selector: str) -> dict:
    """点击页面元素并等待 0.5s，返回跳转前后的 URL。"""
    page = await BrowserSession.get().page()
    before = page.url
    await page.click(selector)
    await page.wait_for_timeout(500)
    after = page.url
    return {"clicked": selector, "url_before": before, "url_after": after}


@_degrade
async def browse_input(ctx: ToolContext, selector: str, text: str) -> dict:
    """清空输入框并填入文本。"""
    page = await BrowserSession.get().page()
    await page.fill(selector, text)
    return {"filled": selector, "text": text}


@_degrade
async def browse_screenshot(ctx: ToolContext, selector: Optional[str] = None) -> dict:
    """截取指定区域（或整页）的 PNG 截图并 Base64 编码返回。"""
    page = await BrowserSession.get().page()
    target = await page.query_selector(selector) if selector else None
    png = await (target.screenshot() if target else page.screenshot())
    b64 = base64.b64encode(png).decode("ascii")
    return {"png_base64": b64, "selector": selector or "fullpage"}
