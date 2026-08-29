"""独立联网搜索客户端（D15：web_search 走独立搜索 API，不依赖 LLM Provider）。

- SEARCH_API_BASE / SEARCH_API_KEY 未配置时返回空列表，工具层降级为"未配置"提示。
- 搜索服务失败静默降级（best-effort），不阻断对话。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 8.0


@dataclass
class SearchResult:
    title: str = ""
    url: str = ""
    snippet: str = ""

    def to_dict(self) -> dict:
        return {"title": self.title, "url": self.url, "snippet": self.snippet}


class SearchClient:
    """独立搜索 API 客户端。search_fn 可注入（测试用 Fake 替代真实 HTTP）。"""

    def __init__(
        self,
        base_url: str = "",
        api_key: str = "",
        search_fn: Optional[Callable[[str, int], list[dict]]] = None,
    ) -> None:
        self.base_url = base_url or settings.search_api_base
        self.api_key = api_key or settings.search_api_key
        self._search_fn = search_fn
        self._client = httpx.AsyncClient(timeout=TIMEOUT_SECONDS)

    @property
    def available(self) -> bool:
        return bool(self.base_url and self.api_key) or self._search_fn is not None

    async def search(self, query: str, limit: int = 5) -> list[dict]:
        """联网搜索：返回 ≤limit 条 {title, url, snippet}；失败/未配置返回 []。"""
        if self._search_fn is not None:
            try:
                results = self._search_fn(query, limit)
                return [r if isinstance(r, dict) else r.to_dict() for r in results][:limit]
            except Exception as exc:  # noqa: BLE001 —— 注入函数失败按空处理
                logger.warning("[web_search] 注入搜索失败：%s", exc)
                return []
        if not self.available:
            return []
        try:
            resp = await self._client.get(
                f"{self.base_url.rstrip('/')}/search",
                params={"q": query, "limit": limit},
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
            resp.raise_for_status()
            data = resp.json()
            hits = data.get("results") or data.get("items") or data.get("data") or []
            return [self._normalize(hit) for hit in hits][:limit]
        except Exception as exc:  # noqa: BLE001 —— 搜索服务不可用 best-effort
            logger.warning("[web_search] 搜索服务不可用：%s", exc)
            return []

    @staticmethod
    def _normalize(hit: Any) -> dict:
        if isinstance(hit, dict):
            return {
                "title": str(hit.get("title") or hit.get("name") or ""),
                "url": str(hit.get("url") or hit.get("link") or ""),
                "snippet": str(hit.get("snippet") or hit.get("summary") or ""),
            }
        return {"title": str(hit), "url": "", "snippet": ""}

    async def aclose(self) -> None:
        await self._client.aclose()


def get_search_client() -> SearchClient:
    """全局搜索客户端单例。"""
    global _global_search
    if _global_search is None:
        _global_search = SearchClient()
    return _global_search


_global_search: SearchClient | None = None
