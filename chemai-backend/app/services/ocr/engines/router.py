"""双引擎路由与降级链（设计 D3 / ADR 0004）。

按文件类型路由：图片→百度 OCR，PDF→MinerU。每层引擎失败才进入下一层
（图片：百度→VLM→部分结果；PDF：MinerU→百度→VLM），成功即返回不回退；
全失败或全部分时返回最后部分结果，不抛未捕获异常。
"""
from __future__ import annotations

from pathlib import Path

import httpx

from app.services.ocr.engines.base import OCRDocument, OCRResult, OCREngineError
from app.services.ocr.engines.baidu_engine import BaiduOCREngine
from app.services.ocr.engines.mineru_engine import MinerUEngine
from app.services.ocr.engines.vlm_fallback import VLMFallbackEngine

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
PDF_EXTS = {".pdf"}


def classify_document(document: OCRDocument) -> str:
    """按扩展名判定文档类型：image / pdf。不支持的类型抛 ValueError。"""
    ext = Path(document.path).suffix.lower()
    if ext in PDF_EXTS:
        return "pdf"
    if ext in IMAGE_EXTS:
        return "image"
    raise ValueError(f"不支持的文件类型: {ext or '(无扩展名)'}")


def build_chain(
    kind: str,
    *,
    http: httpx.AsyncClient | None = None,
    llm_client=None,
) -> list:
    """按文档类型构造降级链（依赖注入 http / llm_client 供测试 mock）。"""
    if kind == "image":
        return [BaiduOCREngine(http=http), VLMFallbackEngine(client=llm_client)]
    if kind == "pdf":
        return [
            MinerUEngine(),
            BaiduOCREngine(http=http),
            VLMFallbackEngine(client=llm_client),
        ]
    raise ValueError(f"未知文档类型: {kind}")


async def extract_document(
    document: OCRDocument,
    *,
    http: httpx.AsyncClient | None = None,
    llm_client=None,
) -> OCRResult:
    """按降级链执行识别：每层失败进入下一层，成功即返回；全失败返回部分结果。"""
    kind = classify_document(document)
    last_partial: OCRResult | None = None
    for engine in build_chain(kind, http=http, llm_client=llm_client):
        try:
            result = await engine.extract(document)
        except OCREngineError:
            continue
        if result.partial:
            last_partial = result
            continue  # 部分结果也继续降级，争取更高质量识别
        return result
    return last_partial or OCRResult(
        provider="none",
        partial=True,
        degraded=True,
        error="全部引擎失败或返回部分结果",
    )
