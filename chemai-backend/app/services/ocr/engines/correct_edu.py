"""百度 correct_edu 批改引擎：协议 stub（ADR 0004）。

correct_edu 是异步任务 + 120s 轮询 + 百度教育专属 API，本阶段不接入，
返回配置缺失提示（前端可见），后续按统一 OCRProvider 协议补齐实现。
"""
from __future__ import annotations

from app.services.ocr.engines.base import OCRDocument, OCRResult, OCREngineError


class CorrectEduEngine:
    name = "correct_edu"

    async def extract(self, document: OCRDocument) -> OCRResult:
        raise OCREngineError("correct_edu 批改暂不可用：未配置百度教育专属凭据")
