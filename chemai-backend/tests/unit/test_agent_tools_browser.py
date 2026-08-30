"""浏览器工具组单测（doc 30 §3.8）：5 工具行为 + 60s 空闲回收 + 不可用降级。"""
import asyncio

import pytest

from app.agents.tools import tools_browser
from app.agents.tools.context import ToolContext
from app.agents.tools.registry import TOOL_IMPLS, TOOL_SCHEMAS
from app.agents.tools.tool_meta import TOOL_META, PERSONAS


def run(coro):
    return asyncio.run(coro)


class FakePage:
    """mock Playwright Page：记录调用并返回确定性结果。"""
    def __init__(self):
        self.url = "http://example.com/start"
        self.goto_calls = []
        self.click_calls = []
        self.fill_calls = []
        self._screenshot_png = b"\x89PNG-fake"

    async def goto(self, url, wait_until=None, timeout=None):
        self.goto_calls.append((url, wait_until))
        self.url = url
        return None

    async def title(self):
        return "示例标题"

    async def inner_text(self, selector):
        return "页面正文内容"

    async def click(self, selector):
        self.click_calls.append(selector)
        self.url = "http://example.com/after"

    async def wait_for_timeout(self, ms):
        return None

    async def fill(self, selector, text):
        self.fill_calls.append((selector, text))
        return None

    async def query_selector(self, selector):
        return self

    async def screenshot(self):
        return self._screenshot_png


class FakeSession:
    """mock BrowserSession.page()，暴露真实实例供断言。"""
    def __init__(self):
        self.page_obj = FakePage()
        self.starts = 0
        self.closes = 0

    async def page(self):
        self.starts += 1
        return self.page_obj

    async def close(self):
        self.closes += 1


@pytest.fixture
def fake_session(monkeypatch):
    sess = FakeSession()
    monkeypatch.setattr(tools_browser.BrowserSession, "get", lambda: sess)
    yield sess
    tools_browser.BrowserSession._instance = None


def _ctx():
    return ToolContext(db=None, user={"user_id": 1, "role": "teacher", "school_id": 1})


# ---------------- 注册表完整性 ----------------

def test_browser_tools_registered_5():
    names = {"browse_navigate", "browse_read", "browse_click", "browse_input", "browse_screenshot"}
    assert names <= set(TOOL_IMPLS)
    assert names <= set(TOOL_SCHEMAS)
    assert names <= set(TOOL_META)
    for n in names:
        assert set(TOOL_META[n].personas) == set(PERSONAS)  # 全角色可用


# ---------------- 各工具行为 ----------------

def test_browse_navigate_returns_title_text(fake_session):
    out = run(tools_browser.browse_navigate(_ctx(), url="http://example.com/page"))
    assert out["title"] == "示例标题"
    assert out["text"] == "页面正文内容"
    assert fake_session.page_obj.goto_calls[0][0] == "http://example.com/page"
    assert len(out["text"]) <= 8000


def test_browse_read_extracts_selector_text(fake_session):
    out = run(tools_browser.browse_read(_ctx(), selector="#main"))
    assert out["selector"] == "#main"
    assert out["text"] == "页面正文内容"


def test_browse_click_returns_url_before_after(fake_session):
    out = run(tools_browser.browse_click(_ctx(), selector="button.submit"))
    assert out["url_before"] == "http://example.com/start"
    assert out["url_after"] == "http://example.com/after"
    assert "button.submit" in fake_session.page_obj.click_calls


def test_browse_input_fills_text(fake_session):
    out = run(tools_browser.browse_input(_ctx(), selector="#search", text="配平"))
    assert out["filled"] == "#search"
    assert out["text"] == "配平"
    assert ("#search", "配平") in fake_session.page_obj.fill_calls


def test_browse_screenshot_returns_base64(fake_session):
    out = run(tools_browser.browse_screenshot(_ctx(), selector=".chart"))
    import base64
    assert base64.b64decode(out["png_base64"]) == b"\x89PNG-fake"
    assert out["selector"] == ".chart"


# ---------------- 降级：浏览器不可用 ----------------

def test_browser_unavailable_returns_structured_error(monkeypatch):
    async def _boom():
        raise RuntimeError("playwright not installed")
    monkeypatch.setattr(tools_browser.BrowserSession, "get", lambda: type("S", (), {"page": _boom})())
    out = run(tools_browser.browse_navigate(_ctx(), url="http://example.com"))
    assert out["error"] == "browser_unavailable"


# ---------------- 60s 空闲回收 ----------------

def test_idle_timeout_recycles_session(monkeypatch):
    """`_last_used` 距今超过 IDLE_TIMEOUT_S → close 旧实例并由 _start 重启。"""
    class FakeBrowser:
        def __init__(self):
            self.closed = False
        async def close(self):
            self.closed = True

    old_browser = FakeBrowser()
    started = []

    async def fake_start(self):
        started.append(1)
        return FakePage()

    monkeypatch.setattr(tools_browser.BrowserSession, "_start", fake_start)
    sess = tools_browser.BrowserSession()
    sess._browser = old_browser
    sess._playwright = object()
    sess._last_used = tools_browser.time.monotonic() - 999  # 距今远超 60s
    monkeypatch.setattr(tools_browser.BrowserSession, "get", lambda: sess)

    out = run(tools_browser.browse_read(_ctx(), selector="#main"))
    assert out["text"] == "页面正文内容"
    assert old_browser.closed          # 旧实例已回收
    assert started == [1]              # 已通过 _start 重启
    assert sess._last_used > 0.0
