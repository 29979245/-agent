"""OCR 引擎公共协议与数据结构（ADR 0004）。

统一 OCRProvider 协议：extract(document) → 结构化结果 OCRResult。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

# 判定"未识别到有效文本"的最小字符数
MIN_OCR_TEXT_CHARS = 10


@dataclass
class OCRDocument:
    """待识别文档：本地文件路径 + 可选 MIME 类型（文件类型主要按扩展名判定）。"""

    path: str
    mime_type: str = ""


@dataclass
class OCRResult:
    """结构化识别结果。partial=True 表示部分/低置信度结果（前端可见）。"""

    student_no: str = "unknown"
    student_name: str = "待识别"
    answers: list[dict] = field(default_factory=list)  # [{"question_no": int, "answer": str}]
    provider: str = ""
    confidence: float = 0.0
    partial: bool = False
    degraded: bool = False
    fallback_used: bool = False
    error: str = ""


class OCRProvider(Protocol):
    """OCR 引擎统一协议：输入文档，输出结构化识别结果。"""

    name: str

    async def extract(self, document: OCRDocument) -> OCRResult: ...


class OCREngineError(RuntimeError):
    """引擎级失败基类：路由捕获后进入下一层降级。"""
